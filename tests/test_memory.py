import importlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from pipeline import memory
from pipeline.memory import load_all, normalize_key, save_characteristic


def proposal(**changes):
    item = {
        "context_kind": "ktru_family",
        "context_key": "31.01.10",
        "name": "Тип ножек",
        "value_type": "text",
        "unit_code": None,
        "unit_name": None,
        "aliases": ["Материал ножек"],
    }
    item.update(changes)
    return item


def test_persists_after_module_restart_and_uses_wal(tmp_path):
    path = tmp_path / "learned.sqlite3"

    saved = save_characteristic(path, proposal())
    restarted = importlib.reload(memory)

    assert saved["action"] == "created"
    assert restarted.load_all(path) == [
        {key: value for key, value in saved.items() if key != "action"}
    ]
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_normalized_name_and_aliases_are_deduplicated(tmp_path):
    path = tmp_path / "learned.sqlite3"

    first = save_characteristic(path, proposal())
    second = save_characteristic(
        path,
        proposal(
            name="  тип-ножек ",
            aliases=["материал ножек", "Конструкция опор", "конструкция   опор"],
        ),
    )

    assert normalize_key(" Ёмкость_БАКА ") == "емкость бака"
    assert first["id"] == second["id"]
    assert second["action"] == "updated"
    assert second["confirmations"] == 2
    assert second["aliases"] == ["Конструкция опор", "Материал ножек"]
    assert len(load_all(path)) == 1


def test_same_name_in_different_scopes_is_isolated(tmp_path):
    path = tmp_path / "learned.sqlite3"

    furniture = save_characteristic(path, proposal())
    tools = save_characteristic(path, proposal(context_key="25.73.30"))

    assert furniture["action"] == "created"
    assert tools["action"] == "created"
    assert furniture["id"] != tools["id"]
    assert len(load_all(path)) == 2


def test_concurrent_upsert_creates_one_characteristic(tmp_path):
    path = tmp_path / "learned.sqlite3"

    def save(index):
        return save_characteristic(path, proposal(aliases=[f"Опоры вариант {index}"]))

    with ThreadPoolExecutor(max_workers=8) as executor:
        saved = list(executor.map(save, range(24)))

    assert sum(item["action"] == "created" for item in saved) == 1
    assert {item["id"] for item in saved} == {saved[0]["id"]}
    stored = load_all(path)
    assert len(stored) == 1
    assert len(stored[0]["aliases"]) == 24
    assert stored[0]["confirmations"] == 24


def test_does_not_store_query_values_or_result(tmp_path):
    path = tmp_path / "learned.sqlite3"
    secret = "СЕКРЕТНОЕ ОПИСАНИЕ ПОЛЬЗОВАТЕЛЯ"
    item = proposal()
    item.update(
        {
            "raw_query": secret,
            "value": "красный товарный образец",
            "result": {"code": "private-result"},
        }
    )

    save_characteristic(path, item)

    stored = path.read_bytes().decode("utf-8", errors="ignore")
    assert secret not in stored
    assert "красный товарный образец" not in stored
    assert "private-result" not in stored


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"context_kind": "global"}, "context_kind"),
        ({"value_type": "object"}, "value_type"),
        ({"unit_code": "003"}, "unit_code and unit_name"),
    ],
)
def test_rejects_values_outside_storage_contract(tmp_path, changes, message):
    with pytest.raises(ValueError, match=message):
        save_characteristic(tmp_path / "learned.sqlite3", proposal(**changes))


def test_returns_unit_as_one_object(tmp_path):
    saved = save_characteristic(
        tmp_path / "learned.sqlite3",
        proposal(value_type="number", unit_code="003", unit_name="Миллиметр"),
    )

    assert saved["unit"] == {"code": "003", "name": "Миллиметр"}
