import httpx
import pytest

import web.backend.main
from web.backend.main import MAX_ROWS, app, csv_texts

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
    assert response.json() == {"text": "стол"}


def test_csv_skips_header_and_empty_rows():
    raw = "Наименование товара\nСтол письменный, ДСП\n\n  \nМолоток 500 г\n".encode()
    assert csv_texts(raw) == ["Стол письменный, ДСП", "Молоток 500 г"]


def test_csv_from_excel_and_quoted():
    assert csv_texts("\ufeffСтол; лишнее\r\nСтул\r\n".encode()) == ["Стол", "Стул"]
    assert csv_texts("Стол, дерево".encode("cp1251")) == ["Стол, дерево"]
    assert csv_texts('"Стол; ""Лофт"""\n'.encode()) == ['Стол; "Лофт"']


async def upload(content: bytes):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/api/csv", content=content)


async def test_csv_endpoint():
    response = await upload("Наименование товара\nСтол\nСтул".encode())
    assert response.json() == {"texts": ["Стол", "Стул"]}


async def test_csv_empty_or_too_big():
    for content in [b"", "Наименование товара\n".encode()]:
        response = await upload(content)
        assert response.status_code == 400
        assert response.json() == {"error": "В файле нет описаний товаров"}

    response = await upload("\n".join(["стол"] * (MAX_ROWS + 1)).encode())
    assert response.status_code == 400
