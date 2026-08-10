from __future__ import annotations

import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


WEB_DIR = Path(__file__).resolve().parents[1] / "web"


class PreviewHandler(BaseHTTPRequestHandler):
    """无需安装 FastAPI 的界面预览服务器；不执行 Agent 查询。"""

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self._json(200, {"status": "ok", "mode": "static-preview"})
            return
        if path == "/":
            self._file(WEB_DIR / "index.html")
            return
        if path in {"/styles.css", "/dashboard.css", "/preview-bootstrap.js", "/app.js"}:
            self._file(WEB_DIR / path.removeprefix("/"))
            return
        if path.startswith("/assets/"):
            requested = (WEB_DIR / path.removeprefix("/assets/")).resolve()
            if WEB_DIR.resolve() not in requested.parents:
                self._json(403, {"detail": "forbidden"})
                return
            self._file(requested)
            return
        self._json(404, {"detail": "not found"})

    def do_POST(self) -> None:
        self._json(
            503,
            {
                "detail": (
                    "当前是静态预览模式。安装 requirements-web.txt 后运行 "
                    "./run_web.sh，即可使用完整 Agent。"
                )
            },
        )

    def _file(self, path: Path) -> None:
        if not path.is_file():
            self._json(404, {"detail": "not found"})
            return
        content = path.read_bytes()
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", f"{mime_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _json(self, status: int, payload: dict) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    host = os.getenv("ASKDATA_HOST", "127.0.0.1")
    port = int(os.getenv("ASKDATA_PORT", "8000"))
    server = ThreadingHTTPServer((host, port), PreviewHandler)
    print(f"AskData static preview: http://{host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
