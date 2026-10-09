from contracts.ref import Characteristic, Position
from pipeline.candidates import find
from pipeline.chars import extract
from pipeline.narrow import narrow

TOP: Characteristic = {
    "code": "top",
    "name": "Толщина материала столешницы",
    "type": "number",
    "selection": "fixed",
    "required": False,
    "values": [{"min": 16, "min_inclusive": True, "max": 25, "max_inclusive": False}],
    "unit": {"code": "003", "name": "Миллиметр"},
}


def frame_char(value: str) -> Characteristic:
    return {
        "code": "frame",
        "name": "Тип каркаса",
        "type": "text",
        "selection": "fixed",
        "required": True,
        "values": [value],
    }


def desk(code: str, frame: str) -> Position:
    return {
        "code": code,
        "name": "Стол письменный",
        "category": "furniture",
        "okpd2_code": "31.01.10.000",
        "position_type": "final",
        "parent_code": "desk",
        "units": [],
        "characteristics": [frame_char(frame), TOP],
    }


REF = [desk("wood", "Деревянный"), desk("metal", "Металлический")]


def run(text: str):
    candidates = find(text, REF)
    return narrow(candidates, extract(text, candidates, REF, {}), REF)


def test_full_description_gives_one_code():
    result = run("Стол письменный деревянный, столешница 2,2 см")

    assert result["category"] == "furniture"
    assert result["code"] == "wood"
    assert [step["left"] for step in result["steps"]] == [2, 1]
    assert {c["name"]: c["value"] for c in result["chars"]} == {
        "Тип каркаса": "Деревянный",
        "Толщина материала столешницы": 22,
    }


def test_several_candidates_give_question():
    result = run("письменного стола")

    assert result["code"] is None
    assert result["question"] == {"char": "Тип каркаса", "options": ["Деревянный", "Металлический"]}
    assert result["missing_required"] == ["Тип каркаса"]


def test_empty_and_unknown_text_give_no_candidates():
    for text in ["", "   ", "шины летние"]:
        result = run(text)
        assert result["category"] is None
        assert result["code"] is None
        assert result["steps"][-1]["left"] == 0


def test_contradicting_value_leaves_no_codes():
    result = run("Стол письменный, столешница 40 мм")

    assert result["code"] is None
    assert result["question"] is None
    assert result["steps"][-1] == {"what": "Толщина материала столешницы: 40", "left": 0}
