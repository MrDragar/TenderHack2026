import httpx
import pytest

import web.server
from web.server import answer, moderate


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def post(**kwargs):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/api/run", **kwargs)


async def request(text: str):
    return await post(json={"text": text})


async def test_empty_text():
    response = await request("   ")
    assert response.status_code == 400
    assert response.json() == {"error": "Опишите товар"}


async def test_no_text_field():
    response = await post(json={})
    assert response.status_code == 400
    assert response.json() == {"error": "Опишите товар"}


async def test_broken_json():
    response = await post(
        content=b"not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json() == {"error": "Опишите товар"}


async def test_wrong_json_shape():
    response = await post(json=[1, 2])
    assert response.status_code == 400
    assert response.json() == {"error": "Опишите товар"}


async def test_too_long():
    response = await request("а" * 1001)
    assert response.status_code == 400
    assert response.json() == {"error": "Слишком длинное описание"}


async def test_pipeline_crash(monkeypatch):
    def boom(text):
        raise RuntimeError(text)

    monkeypatch.setattr(web.backend.main, "run", boom)
    response = await request("стол")
    assert response.status_code == 500
    assert response.json() == {"error": "Не удалось разобрать описание"}


def test_passes_stripped_text(monkeypatch):
    monkeypatch.setattr(web.server, "run", lambda text: {"text": text})
    assert answer(request("  стол  ")) == (200, {"text": "стол"})


def decision(**changed) -> bytes:
    sent = {"told": "ручка", "name": "Ручка", "product": "Молоток", "approved": True}
    return json.dumps(sent | changed).encode()


def test_moderate_saves_decision(monkeypatch):
    saved = []
    monkeypatch.setattr(web.server, "decide", saved.append)

    assert moderate(decision(told=" ручка ")) == (200, {"ok": True})
    assert saved == [{"told": "ручка", "name": "Ручка", "product": "Молоток", "approved": True}]


def test_moderate_rejects_broken_input(monkeypatch):
    monkeypatch.setattr(web.server, "decide", lambda d: None)

    assert moderate(b"not json")[0] == 400
    assert moderate(b"[]")[0] == 400
    assert moderate(decision(name="  "))[0] == 400
    assert moderate(decision(approved="да"))[0] == 400
