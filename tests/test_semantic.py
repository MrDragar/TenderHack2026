import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from contracts.pipeline import Candidates, Interpretation, Knowledge, PipelineSteps, Result
from contracts.ref import Characteristic, Portal, Position, Value
from pipeline.memory import load_all, save_characteristic
from pipeline.semantic import run
from scripts.measure_semantic import measure_scenarios


def text_char(
    code: str,
    name: str,
    values: Sequence[Value],
    *,
    required: bool = False,
    selection: Literal["fixed", "single", "multiple"] = "single",
) -> Characteristic:
    item: Characteristic = {
        "code": code,
        "name": name,
        "type": "text",
        "selection": selection,
        "required": required,
        "values": list(values),
    }
    return item


HEIGHT: Characteristic = {
    "code": "height",
    "name": "Высота",
    "type": "number",
    "selection": "single",
    "required": True,
    "values": [{"min": 700, "min_inclusive": True, "max": 800, "max_inclusive": True}],
    "unit": {"code": "003", "name": "Миллиметр"},
}
WIDTH: Characteristic = {
    "code": "width",
    "name": "Ширина",
    "type": "number",
    "selection": "single",
    "required": True,
    "values": [1200],
    "unit": {"code": "003", "name": "Миллиметр"},
}


def desk(code: str, frame: str) -> Position:
    return {
        "code": code,
        "name": "Стол письменный",
        "category": "furniture",
        "okpd2_code": "31.01.10",
        "position_type": "final",
        "parent_code": "desk",
        "units": [],
        "characteristics": [
            text_char("frame", "Тип каркаса", [frame], required=True, selection="fixed"),
            HEIGHT,
            WIDTH,
        ],
    }


REF = [desk("wood", "Деревянный"), desk("metal", "Металлический")]
PORTAL: Portal = {
    "Столы письменные": [
        {"name": "Цвет", "type": "Справочник", "unit": None, "values": ["Серый", "Белый"]},
        {"name": "Кабель-канал", "type": "Текст", "unit": None, "values": []},
    ],
    "Органайзеры для инструментов": [
        {"name": "Цвет", "type": "Справочник", "unit": None, "values": ["Красный"]},
        {"name": "Материал", "type": "Справочник", "unit": None, "values": ["Пластик"]},
    ],
}


def semantic(text: str, tmp_path, *, learn: bool = False, ref=REF, portal=PORTAL):
    return run(
        text,
        learn,
        memory_path=tmp_path / "learned.sqlite3",
        positions=ref,
        portal=portal,
    )


def by_name(result: Result) -> dict:
    return {char["name"]: char for char in result["chars"]}


def test_combines_ktru_and_portal_and_converts_units(tmp_path):
    result = semantic(
        "Стол письменный металлический, высота 0,75 м, ширина 1200 мм, Цвет: серый",
        tmp_path,
    )

    chars = by_name(result)
    assert result["code"] == "metal"
    assert result["status"] == "ready"
    assert result["portal_category"] == "Столы письменные"
    assert (chars["Тип каркаса"]["source"], chars["Тип каркаса"]["value"]) == (
        "ktru",
        "Металлический",
    )
    assert (chars["Высота"]["value"], chars["Высота"]["unit"]["code"]) == (750, "003")
    assert (chars["Цвет"]["source"], chars["Цвет"]["value"]) == ("portal", "Серый")
    assert "высота 750 миллиметр" in result["corrected_text"]


def test_preview_does_not_write_then_submit_teaches_and_next_request_reuses(tmp_path):
    text = "Стол письменный металлический, Кромка столешницы: АБС"

    preview = semantic(text, tmp_path)
    assert by_name(preview)["Кромка столешницы"]["source"] == "new"
    assert preview["learning"][0]["action"] == "pending"
    assert not (tmp_path / "learned.sqlite3").exists()
    assert load_all(tmp_path / "learned.sqlite3") == []

    committed = semantic(text, tmp_path, learn=True)
    assert committed["learning"][0]["action"] == "created"
    assert by_name(committed)["Кромка столешницы"]["source"] == "new"

    reused = semantic("Стол письменный металлический, Кромка столешницы: ПВХ", tmp_path)
    assert by_name(reused)["Кромка столешницы"]["source"] == "learned"
    assert reused["learning"][0]["action"] == "reused"
    assert len(load_all(tmp_path / "learned.sqlite3")) == 1


def test_garbage_is_reported_and_never_saved(tmp_path):
    result = semantic(
        "Стол письменный, вкус: клубничный, лучший: да, скидка: 20%",
        tmp_path,
        learn=True,
    )

    assert result["chars"] == []
    assert [item["fragment"] for item in result["rejected"]] == [
        "вкус: клубничный",
        "лучший: да",
        "скидка: 20%",
    ]
    assert result["learning"] == []
    assert load_all(tmp_path / "learned.sqlite3") == []


def test_characteristic_of_another_product_is_removed(tmp_path):
    result = semantic(
        "Стол письменный металлический, Размер обуви: 42",
        tmp_path,
        learn=True,
    )

    assert "Размер обуви" not in by_name(result)
    assert result["rejected"][0]["fragment"] == "Размер обуви: 42"
    assert result["rejected"][0]["reason"] == "характеристика относится к другому виду товара"
    assert load_all(tmp_path / "learned.sqlite3") == []


def test_portal_only_still_accepts_and_teaches_new_field(tmp_path):
    result = semantic(
        "Органайзер для инструментов, Цвет: красный, Материал: пластик, Тип защёлки: поворотная",
        tmp_path,
        learn=True,
        ref=[],
    )

    chars = by_name(result)
    assert result["status"] == "portal_only"
    assert result["code"] is None
    assert chars["Цвет"]["source"] == "portal"
    assert chars["Материал"]["source"] == "portal"
    assert chars["Тип защёлки"]["source"] == "new"
    assert result["learning"][0]["action"] == "created"


def test_product_outside_both_catalogs_gets_isolated_memory(tmp_path):
    learned = semantic(
        "Дрон гоночный, Диагональ рамы: 5 дюймов",
        tmp_path,
        learn=True,
        ref=[],
        portal={},
    )
    reused = semantic(
        "Дрон гоночный, Диагональ рамы: 6 дюймов",
        tmp_path,
        ref=[],
        portal={},
    )
    other = semantic(
        "Кресло офисное, Диагональ рамы: 6 дюймов",
        tmp_path,
        ref=[],
        portal={},
    )

    assert learned["status"] == "learned_only"
    assert by_name(learned)["Диагональ рамы"]["source"] == "new"
    assert by_name(reused)["Диагональ рамы"]["source"] == "learned"
    assert by_name(other)["Диагональ рамы"]["source"] == "new"


def test_learned_name_is_matched_semantically_and_rejects_incompatible_unit(tmp_path):
    semantic(
        "Дрон гоночный, Диагональ рамы: 5 дюймов",
        tmp_path,
        learn=True,
        ref=[],
        portal={},
    )

    typo = semantic(
        "Дрон гоночный, Диагональ раммы: 6 дюймов",
        tmp_path,
        learn=True,
        ref=[],
        portal={},
    )
    conflict = semantic(
        "Дрон гоночный, Диагональ рамы: 5 кг",
        tmp_path,
        ref=[],
        portal={},
    )

    assert by_name(typo)["Диагональ рамы"]["source"] == "learned"
    assert by_name(typo)["Диагональ рамы"]["status"] == "corrected"
    assert "Диагональ рамы: 6 дюйм" in typo["corrected_text"]
    assert by_name(conflict)["Диагональ рамы"]["kind"] == "conflict"
    assert conflict["questions"][0]["kind"] == "confirmation"
    stored = load_all(tmp_path / "learned.sqlite3")[0]
    assert stored["confirmations"] == 2
    assert stored["aliases"] == ["Диагональ раммы"]


def test_ambiguity_precedes_required_questions(tmp_path):
    ambiguous = semantic("Стол письменный", tmp_path)
    selected = semantic("Стол письменный металлический", tmp_path)

    assert ambiguous["code"] is None
    assert ambiguous["questions"] == [
        {
            "char": "Тип каркаса",
            "options": ["Деревянный", "Металлический"],
            "kind": "disambiguation",
        }
    ]
    assert ambiguous["missing_required"] == []
    assert selected["code"] == "metal"
    assert [(item["char"], item["kind"]) for item in selected["questions"]] == [
        ("Высота", "required"),
        ("Ширина", "required"),
    ]


def test_typos_are_visible_in_corrected_text(tmp_path):
    result = semantic(
        "cnjk письмениый, металический, высота 0,75 м, ширина 1200 мм",
        tmp_path,
    )

    assert result["code"] == "metal"
    assert {edit["kind"] for edit in result["edits"]} >= {"layout", "typo", "unit"}
    assert "стол письменный" in result["corrected_text"].casefold()
    assert "металлический" in result["corrected_text"].casefold()
    assert "750 миллиметр" in result["corrected_text"].casefold()


def test_ktru_beats_portal_and_learned_for_same_name(tmp_path):
    ref = [desk("metal", "Металлический")]
    ref[0]["characteristics"].append(text_char("color", "Цвет", ["Серый"]))
    save_characteristic(
        tmp_path / "learned.sqlite3",
        {
            "context_kind": "ktru_family",
            "context_key": "desk",
            "name": "Цвет",
            "value_type": "text",
            "unit_code": None,
            "unit_name": None,
            "aliases": [],
        },
    )

    result = semantic("Стол письменный металлический, Цвет: серый", tmp_path, ref=ref)

    assert by_name(result)["Цвет"]["source"] == "ktru"


def test_storage_failure_does_not_break_main_result(tmp_path):
    def broken_save(path, proposal):
        raise OSError("read only")

    result = run(
        "Дрон гоночный, Диагональ рамы: 5 дюймов",
        True,
        memory_path=tmp_path / "learned.sqlite3",
        positions=[],
        portal={},
        save_learned=broken_save,
    )

    assert result["status"] == "learned_only"
    assert result["learning"][0]["action"] == "failed"
    assert by_name(result)["Диагональ рамы"]["source"] == "new"


def test_pipeline_steps_are_replaceable_by_contract(tmp_path):
    calls = []

    def retrieve(text: str, knowledge: Knowledge) -> Candidates:
        calls.append("retrieve")
        return {"category": None, "candidates": []}

    def interpret(text: str, candidates: Candidates, knowledge: Knowledge) -> Interpretation:
        calls.append("interpret")
        return {"chars": [], "rejected": [], "portal_category": None}

    def decide(
        candidates: Candidates, interpretation: Interpretation, knowledge: Knowledge
    ) -> Result:
        calls.append("decide")
        return {
            "category": None,
            "steps": [],
            "chars": [],
            "code": None,
            "question": None,
            "missing_required": [],
            "remarks": [],
            "candidates": [],
            "options": {},
            "unknown": [],
            "status": "not_found",
            "questions": [],
            "rejected": [],
            "portal_category": None,
        }

    steps: PipelineSteps = {"retrieve": retrieve, "interpret": interpret, "decide": decide}
    result = run(
        "неизвестный товар",
        memory_path=tmp_path / "learned.sqlite3",
        positions=[],
        portal={},
        steps=steps,
    )

    assert calls == ["retrieve", "interpret", "decide"]
    assert result["implementation"] == "semantic-hybrid-v1"


def test_database_contains_schema_but_not_input_or_values(tmp_path):
    secret = "СЕКРЕТНОЕ ОПИСАНИЕ"
    path = tmp_path / "learned.sqlite3"
    semantic(
        f"Дрон гоночный {secret}, Диагональ рамы: 5 дюймов",
        tmp_path,
        learn=True,
        ref=[],
        portal={},
    )

    with sqlite3.connect(path) as connection:
        sql = " ".join(row[0] or "" for row in connection.execute("SELECT sql FROM sqlite_master"))
        names = " ".join(
            row[0] for row in connection.execute("SELECT name FROM learned_characteristics")
        )
    assert secret not in sql + names
    assert "5 дюймов" not in path.read_bytes().decode("utf-8", errors="ignore")


def test_documented_scenarios_are_executable():
    from examples.semantic_runner import runner

    path = Path(__file__).parents[1] / "examples" / "semantic_scenarios.json"
    with path.open(encoding="utf-8") as source:
        scenarios = json.load(source)

    metrics = measure_scenarios(scenarios, runner)

    assert metrics["safe_success"] == 1
    assert metrics["char_f1"] == 1
    assert metrics["reuse_recall"] == 1
    assert metrics["garbage_write_rate"] == 0
    assert metrics["wrong_scope_leakage_rate"] == 0
