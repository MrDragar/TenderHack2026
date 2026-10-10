import asyncio
import json
import logging
import mimetypes
import sys
from contextlib import asynccontextmanager
from http import HTTPStatus
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from pipeline.semantic import portal_reference, reference, run

STATIC = Path(__file__).parent / "static"
STATIC_ROOT = STATIC.resolve()
MAX_TEXT = 1000


def answer(body: bytes) -> tuple[int, object]:
    try:
        payload = json.loads(body)
    except ValueError:
        payload = {}
    text = payload.get("text") if isinstance(payload, dict) else None
    learn = payload.get("learn", False) if isinstance(payload, dict) else False
    if not isinstance(text, str) or not text.strip():
        return HTTPStatus.BAD_REQUEST, {"error": "Опишите товар"}
    if len(text) > MAX_TEXT:
        return HTTPStatus.BAD_REQUEST, {"error": "Слишком длинное описание"}
    try:
        return HTTPStatus.OK, run(text.strip(), learn=learn is True)
    except Exception as error:
        logging.error("pipeline failed: %s", type(error).__name__)
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Не удалось разобрать описание"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    reference()
    portal_reference()
    yield


app = FastAPI(title="Tender Hack Казань 2026", lifespan=lifespan)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/run")
async def api_run(request: Request):
    status, payload = answer(await request.body())
    return JSONResponse(payload, status_code=status, headers={"Cache-Control": "no-store"})


def static_response(asset_path: str) -> Response:
    if asset_path in {"", "app", "index"}:
        asset_path = "index.html"
    if asset_path.startswith("static/"):
        asset_path = asset_path.removeprefix("static/")
    target = (STATIC_ROOT / asset_path).resolve()
    if not target.is_relative_to(STATIC_ROOT) or not target.is_file():
        raise HTTPException(HTTPStatus.NOT_FOUND)
    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    content = target.read_bytes()

    async def chunks():
        for start in range(0, len(content), 1024):
            yield content[start : start + 1024]
            # На Python 3.14 без переключения цикла крупный локальный ответ зависает в сокете.
            await asyncio.sleep(0)

    return StreamingResponse(
        chunks(),
        media_type=media_type,
        headers={"Cache-Control": "no-store"},
    )


@app.get("/")
async def frontend():
    return static_response("index.html")


@app.get("/{asset_path:path}")
async def assets(asset_path: str):
    return static_response(asset_path)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"http://127.0.0.1:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
