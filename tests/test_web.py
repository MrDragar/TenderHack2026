import asyncio
import json

import httpx

import web.server
from web.server import answer, app


def request(text: str, *, learn: bool = False) -> bytes:
    return json.dumps({"text": text, "learn": learn}).encode()


def get(path: str) -> httpx.Response:
    async def send():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path)

    return asyncio.run(send())


def post(path: str, payload: dict) -> httpx.Response:
    async def send():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(path, json=payload)

    return asyncio.run(send())


def test_empty_text():
    assert answer(request("   ")) == (400, {"error": "Опишите товар"})


def test_no_text_field():
    assert answer(b"{}")[0] == 400


def test_broken_json():
    assert answer(b"not json")[0] == 400
    assert answer(b"[1, 2]")[0] == 400


def test_too_long():
    assert answer(request("а" * 1001)) == (400, {"error": "Слишком длинное описание"})


def test_pipeline_crash_does_not_log_user_text(monkeypatch, caplog):
    def boom(text, *, learn):
        raise RuntimeError(text)

    monkeypatch.setattr(web.server, "run", boom)
    assert answer(request("стол")) == (500, {"error": "Не удалось разобрать описание"})
    assert "стол" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_passes_stripped_text(monkeypatch):
    monkeypatch.setattr(web.server, "run", lambda text, *, learn: {"text": text, "learn": learn})
    assert answer(request("  стол  ")) == (200, {"text": "стол", "learn": False})
    assert answer(request("  стол  ", learn=True)) == (200, {"text": "стол", "learn": True})


def test_frontend_returns_html():
    response = get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    assert '<form id="form"' in response.text


def test_static_alias_returns_html():
    response = get("/static/index.html")

    assert response.status_code == 200
    assert '<form id="form"' in response.text


def test_large_static_file_is_complete():
    response = get("/app.js")

    assert response.status_code == 200
    assert response.content == (web.server.STATIC / "app.js").read_bytes()


def test_health():
    assert get("/api/health").json() == {"status": "ok"}


def test_api_route(monkeypatch):
    monkeypatch.setattr(web.server, "run", lambda text, *, learn: {"text": text, "learn": learn})

    response = post("/api/run", {"text": "  стол  ", "learn": True})

    assert response.status_code == 200
    assert response.json() == {"text": "стол", "learn": True}
