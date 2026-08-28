"""웹 서버 — `python -m trading.web` (기동은 ops/start-report-site.sh, 테일넷 전용 바인드).

라우팅: / 대시보드 · /reports 보고서 색인+파일 · /stocks·/industries·/files 는 W2/W3 예정.
전부 GET 읽기 전용 — 판정·집행 조작 경로 없음(P-15 원칙).
"""

import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from trading.report_site import REPORT_DIR, safe_path, wrap_markdown
from trading.web.dashboard import render_dashboard


class WebHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — http.server 규약
        parsed = urlparse(self.path)
        route = parsed.path
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        try:
            if route in ("", "/", "/index.html"):
                self._html(render_dashboard())
            elif route == "/stocks":
                from trading.web.stocks import render_list

                self._html(render_list())
            elif route.startswith("/stocks/"):
                from trading.web.stocks import render_detail

                self._html_or_404(render_detail(route.removeprefix("/stocks/")), "종목 없음")
            elif route == "/industries":
                from trading.web.industries import render_industries_list

                self._html(render_industries_list())
            elif route.startswith("/industries/"):
                from trading.web.industries import render_industry_detail

                group = unquote(route.removeprefix("/industries/"))
                self._html_or_404(render_industry_detail(group), "산업 그룹 없음")
            elif route == "/reports":
                from trading.web.reports_page import render_reports

                self._html(render_reports(week=query.get("week"), tab=query.get("tab", "weekly")))
            elif route == "/files":
                from trading.web.files_page import render_files

                self._html(render_files())
            elif route.startswith("/files/db/"):
                self._db_snapshot(unquote(route.removeprefix("/files/db/")))
            elif route.startswith("/files/raw/"):
                self._file(route.removeprefix("/files/raw"), attachment=True)
            elif route.startswith("/files/"):
                self._csv(route.removeprefix("/files/"))
            else:
                self._file(route)
        except BrokenPipeError:
            pass
        except Exception as exc:  # noqa: BLE001 — 뷰 오류가 서버를 죽이지 않는다
            self._send(500, "text/plain; charset=utf-8", f"오류: {exc}".encode())

    def _csv(self, name: str) -> None:
        from trading.web.files_page import CSV_BUILDERS

        builder = CSV_BUILDERS.get(name)
        if builder is None:
            self._send(404, "text/plain; charset=utf-8", "not found".encode())
            return
        body = builder().encode("utf-8-sig")  # 엑셀 한글 호환 BOM
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f"attachment; filename={name}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _db_snapshot(self, name: str) -> None:
        from trading.web.files_page import DATA_DIR

        target = (DATA_DIR / name).resolve()
        if (
            "/" in name
            or target.suffix != ".sqlite"
            or target.parent != DATA_DIR.resolve()
            or not target.is_file()
        ):
            self._send(404, "text/plain; charset=utf-8", "not found".encode())
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition", f"attachment; filename={name}")
        self.send_header("Content-Length", str(target.stat().st_size))
        self.end_headers()
        with target.open("rb") as fh:  # 대용량(market 수백 MB) — 스트리밍
            while chunk := fh.read(1 << 20):
                self.wfile.write(chunk)

    def _file(self, route: str, *, attachment: bool = False) -> None:
        target = safe_path(REPORT_DIR, route)
        if target is None:
            self._send(404, "text/plain; charset=utf-8", "not found".encode())
        elif attachment:
            body = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", f"attachment; filename={target.name}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif target.suffix == ".html":
            self._send(200, "text/html; charset=utf-8", target.read_bytes())
        elif target.suffix == ".md":
            self._html(wrap_markdown(target.read_text(encoding="utf-8"), target.name))
        else:
            self._send(200, "application/octet-stream", target.read_bytes())

    def _html(self, body: str) -> None:
        self._send(200, "text/html; charset=utf-8", body.encode())

    def _html_or_404(self, body: str | None, msg: str) -> None:
        if body is None:
            self._send(404, "text/plain; charset=utf-8", msg.encode())
        else:
            self._html(body)

    def _send(self, code: int, ctype: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        pass


def main() -> int:
    host = os.environ.get("REPORT_SITE_HOST", "127.0.0.1")
    port = int(os.environ.get("REPORT_SITE_PORT", "8787"))
    if not REPORT_DIR.is_dir():
        print(f"보고서 디렉터리 없음: {REPORT_DIR}", file=sys.stderr)
        return 1
    server = ThreadingHTTPServer((host, port), WebHandler)
    print(f"트레이딩 웹: http://{host}:{port}/ (읽기 전용, 테일넷 전용 바인드 권장)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


__all__ = ["WebHandler", "main"]
