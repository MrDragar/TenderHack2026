import httpx
import pytest

import web.backend.main
from web.backend.main import app

pytestmark = pytest.mark.anyio


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


async def test_passes_stripped_text(monkeypatch):
    monkeypatch.setattr(web.backend.main, "run", lambda text: {"text": text})
    response = await request("  стол  ")
    assert response.status_code == 200
    assert response.json() == {"text": "стол"}
