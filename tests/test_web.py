import json

import web.server
from web.server import answer


def request(text: str) -> bytes:
    return json.dumps({"text": text}).encode()


def test_empty_text():
    assert answer(request("   ")) == (400, {"error": "Опишите товар"})


def test_no_text_field():
    assert answer(b"{}")[0] == 400


def test_broken_json():
    assert answer(b"not json")[0] == 400
    assert answer(b"[1, 2]")[0] == 400


def test_too_long():
    assert answer(request("а" * 1001)) == (400, {"error": "Слишком длинное описание"})


def test_pipeline_crash(monkeypatch):
    def boom(text):
        raise RuntimeError(text)

    monkeypatch.setattr(web.server, "run", boom)
    assert answer(request("стол")) == (500, {"error": "Не удалось разобрать описание"})


def test_passes_stripped_text(monkeypatch):
    monkeypatch.setattr(web.server, "run", lambda text: {"text": text})
    assert answer(request("  стол  ")) == (200, {"text": "стол"})
