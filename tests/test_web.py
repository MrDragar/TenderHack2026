import json

import web.server
from web.server import answer, moderate


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
