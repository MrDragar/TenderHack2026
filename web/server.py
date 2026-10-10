import json
import logging
import sys
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pipeline.run import reference, run

STATIC = Path(__file__).parent / "static"
MAX_TEXT = 1000


def answer(body: bytes) -> tuple[int, object]:
    try:
        text = json.loads(body).get("text")
    except ValueError, AttributeError:
        text = None
    if not isinstance(text, str) or not text.strip():
        return HTTPStatus.BAD_REQUEST, {"error": "Опишите товар"}
    if len(text) > MAX_TEXT:
        return HTTPStatus.BAD_REQUEST, {"error": "Слишком длинное описание"}
    try:
        return HTTPStatus.OK, run(text.strip())
    except Exception:
        logging.exception("pipeline failed on %r", text)
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Не удалось разобрать описание"}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def do_POST(self):
        if self.path != "/api/run":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        size = int(self.headers.get("Content-Length") or 0)
        status, payload = answer(self.rfile.read(size))
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    # Справочник грузится заранее, чтобы первый запрос не ждал загрузки.
    reference()
    print(f"http://localhost:{port}")
    ThreadingHTTPServer(("", port), Handler).serve_forever()
