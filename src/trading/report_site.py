"""보고서 웹 뷰 — `.runtime/reports/`를 Tailscale 사설망에 읽기 전용 서빙.

`python -m trading.report_site` (기동은 ``ops/start-report-site.sh`` — tmux, 게이트웨이와
동일 패턴). 바인드 주소는 ``REPORT_SITE_HOST``(.env 주입 — 기본 127.0.0.1 안전값,
운영은 테일넷 IP로 주입해 외부 비노출). 쓰기 경로 없음 — GET 전용, 디렉터리 탈출 차단.

Telegram은 알림(P0) 채널로만 쓰고 보고서 열람은 웹으로(운영자 2026-08-28 — 가독성).
"""

import html
import os
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

REPORT_DIR = (Path(".runtime") / "reports").resolve()

_CSS = """
  body { margin:0; background:#f7f8fa; color:#1a202c; line-height:1.6;
         font-family:"Apple SD Gothic Neo","Pretendard","Noto Sans KR",sans-serif; }
  .wrap { max-width:760px; margin:0 auto; padding:28px 18px 60px; }
  h1 { font-size:21px; } h2 { font-size:16px; margin:24px 0 8px; padding-left:8px;
       border-left:4px solid #2b6cb0; }
  a { color:#2b6cb0; text-decoration:none; } a:hover { text-decoration:underline; }
  ul { list-style:none; padding:0; } li { background:#fff; border:1px solid #e2e8f0;
       border-radius:8px; padding:9px 14px; margin:6px 0; }
  .meta { color:#5a6472; font-size:12px; float:right; }
  pre.md { background:#fff; border:1px solid #e2e8f0; border-radius:8px; padding:16px;
           white-space:pre-wrap; word-break:break-word; font-size:13px;
           font-family:"SF Mono",Menlo,monospace; }
  .top { font-size:12.5px; margin-bottom:14px; }
"""


def _entry(base: Path, p: Path) -> str:
    rel = p.relative_to(base).as_posix()
    mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M")
    return f"<li><a href='/{rel}'>{html.escape(rel)}</a><span class='meta'>{mtime}</span></li>"


def build_index(report_dir: Path) -> str:
    """보고서 색인 — 주간 보고서(html 우선)·심사 패킷·기타, 최신순."""
    weekly = sorted(report_dir.glob("weekly-*.html"), reverse=True)
    weekly_md = sorted(report_dir.glob("weekly-*.md"), reverse=True)
    dossiers = sorted((report_dir / "dossiers").glob("*.md"), reverse=True)
    others = sorted(
        p for p in report_dir.glob("*.html") if not p.name.startswith("weekly-")
    )
    parts = [
        "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>트레이딩 보고서</title><style>{_CSS}</style></head><body><div class='wrap'>",
        "<h1>트레이딩 보고서</h1>",
        "<div class='top'>페이퍼 모드, 읽기 전용. 주간 보고서는 토 09:30, 일간 축적은 평일 18:00 자동 갱신.</div>",
        "<h2>주간 보고서</h2><ul>",
        *(_entry(report_dir, p) for p in weekly),
        "</ul><h2>심사 패킷</h2><ul>",
        *(_entry(report_dir, p) for p in dossiers),
        "</ul><h2>기타 문서</h2><ul>",
        *(_entry(report_dir, p) for p in others),
        *(_entry(report_dir, p) for p in weekly_md),
        "</ul></div></body></html>",
    ]
    return "\n".join(parts)


def safe_path(report_dir: Path, url_path: str) -> Path | None:
    """URL 경로 → 보고서 디렉터리 내부 파일. 탈출·비파일은 None."""
    raw = unquote(urlparse(url_path).path).lstrip("/")
    if not raw:
        return None
    candidate = (report_dir / raw).resolve()
    if not str(candidate).startswith(str(report_dir) + os.sep):
        return None
    return candidate if candidate.is_file() else None


def wrap_markdown(text: str, title: str) -> str:
    return (
        "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{_CSS}</style></head><body><div class='wrap'>"
        f"<div class='top'><a href='/'>← 목록</a></div>"
        f"<pre class='md'>{html.escape(text)}</pre></div></body></html>"
    )


class ReportHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — http.server 규약
        if urlparse(self.path).path in ("", "/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", build_index(REPORT_DIR).encode())
            return
        target = safe_path(REPORT_DIR, self.path)
        if target is None:
            self._send(404, "text/plain; charset=utf-8", "not found".encode())
            return
        if target.suffix == ".html":
            self._send(200, "text/html; charset=utf-8", target.read_bytes())
        elif target.suffix == ".md":
            body = wrap_markdown(target.read_text(encoding="utf-8"), target.name)
            self._send(200, "text/html; charset=utf-8", body.encode())
        else:
            self._send(200, "application/octet-stream", target.read_bytes())

    def _send(self, code: int, ctype: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:  # 접근 로그는 조용히
        pass


def main() -> int:
    host = os.environ.get("REPORT_SITE_HOST", "127.0.0.1")
    port = int(os.environ.get("REPORT_SITE_PORT", "8787"))
    if not REPORT_DIR.is_dir():
        print(f"보고서 디렉터리 없음: {REPORT_DIR}", file=sys.stderr)
        return 1
    server = ThreadingHTTPServer((host, port), ReportHandler)
    print(f"보고서 사이트: http://{host}:{port}/ (읽기 전용, {REPORT_DIR})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


__all__ = ["REPORT_DIR", "ReportHandler", "build_index", "safe_path", "wrap_markdown"]


if __name__ == "__main__":
    raise SystemExit(main())
