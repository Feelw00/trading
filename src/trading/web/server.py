"""웹 서버 — `python -m trading.web` (기동은 ops/start-report-site.sh, 테일넷 전용 바인드).

라우팅: / 대시보드 · /reports 보고서 색인+파일 · /stocks·/industries·/files 는 W2/W3 예정.
전부 GET 읽기 전용 — 판정·집행 조작 경로 없음(P-15 원칙).
"""

import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from trading.report_site import REPORT_DIR, build_index, safe_path, wrap_markdown
from trading.web.dashboard import render_dashboard
from trading.web.layout import placeholder


class WebHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — http.server 규약
        route = urlparse(self.path).path
        try:
            if route in ("", "/", "/index.html"):
                self._html(render_dashboard())
            elif route == "/stocks":
                from trading.web.stocks import render_list

                self._html(render_list())
            elif route.startswith("/stocks/"):
                from trading.web.stocks import render_detail

                body = render_detail(route.removeprefix("/stocks/"))
                if body is None:
                    self._send(404, "text/plain; charset=utf-8", "종목 없음".encode())
                else:
                    self._html(body)
            elif route == "/industries":
                self._html(placeholder("산업", "W3", active="/industries"))
            elif route == "/files":
                self._html(placeholder("자료실", "W3", active="/files"))
            elif route == "/reports":
                self._html(build_index(REPORT_DIR))
            else:
                self._file(route)
        except Exception as exc:  # noqa: BLE001 — 뷰 오류가 서버를 죽이지 않는다
            self._send(500, "text/plain; charset=utf-8", f"오류: {exc}".encode())

    def _file(self, route: str) -> None:
        target = safe_path(REPORT_DIR, route)
        if target is None:
            self._send(404, "text/plain; charset=utf-8", "not found".encode())
        elif target.suffix == ".html":
            self._send(200, "text/html; charset=utf-8", target.read_bytes())
        elif target.suffix == ".md":
            self._html(wrap_markdown(target.read_text(encoding="utf-8"), target.name))
        else:
            self._send(200, "application/octet-stream", target.read_bytes())

    def _html(self, body: str) -> None:
        self._send(200, "text/html; charset=utf-8", body.encode())

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
