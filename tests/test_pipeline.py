from contracts.ref import Characteristic, Portal, Position
from pipeline.candidates import find
from pipeline.extract import extract
from pipeline.link import link
from pipeline.narrow import narrow
from pipeline.section import find_section

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
PORTAL: Portal = {
    "Столы письменные": [
        {"name": "Цвет", "type": "Справочник", "unit": None, "values": ["3022 красная", "Красный"]},
        {"name": "Вес", "type": "Дробное числовое значение", "unit": "(кг)", "values": []},
        {"name": "Вид стола", "type": "Справочник", "unit": None, "values": ["Письменный"]},
        {"name": "Размер", "type": "Справочник", "unit": None, "values": ["Размер 9, l"]},
    ],
    "Стулья": [],
}


def run(text: str, portal: Portal | None = None):
    candidates = find(text, REF)
    section = find_section(text, portal or {})
    return narrow(candidates, link(extract(text), candidates, section, REF, portal or {}), REF)


def test_full_description_gives_one_code():
    result = run("Стол письменный деревянный, столешница 2,2 см")

    assert result["category"] == "furniture"
    assert result["code"] == "wood"
    assert [step["left"] for step in result["steps"]] == [2, 2, 2, 1]
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


def test_value_outside_ktru_is_flagged_and_keeps_codes():
    result = run("Стол письменный, столешница 40 мм")

    assert result["chars"][0]["kind"] == "out_of_range"
    assert result["steps"][-1]["ignored"]
    assert result["steps"][-1]["left"] == 2
    assert result["question"] is not None


def test_shifted_unit_is_fixed():
    result = run("Стол письменный, столешница 2 мм")

    assert result["chars"][0]["value"] == 20
    assert result["chars"][0]["kind"] == "wrong_unit"


def test_layout_and_typo_are_fixed_with_reason():
    result = run("cnjk письменный деревяный")

    assert result["code"] == "wood"
    assert result["chars"][0]["kind"] == "typo"


def test_told_value_not_in_ktru_is_kept_with_remark():
    result = run("Стол письменный, Тип каркаса: пластик, цвет: красный")

    by_name = {c["name"]: c for c in result["chars"]}
    assert by_name["Тип каркаса"]["kind"] == "not_in_list"
    assert by_name["Цвет"]["source"] == "new"
    assert result["code"] is None


def test_told_value_of_second_candidate_is_not_flagged():
    result = run("Стол письменный, Тип каркаса: металлический")

    frame = [c for c in result["chars"] if c["name"] == "Тип каркаса"][0]
    assert frame["value"] == "Металлический"
    assert frame["kind"] is None
    assert result["code"] == "metal"


def test_unit_conversion_gives_remark():
    result = run("Стол письменный деревянный, столешница 2,2 см")

    assert result["remarks"] == [
        {
            "was": "2,2 см",
            "now": "Толщина материала столешницы: 22 миллиметр",
            "why": "в КТРУ единица миллиметр, перевели",
            "kind": "unit",
        }
    ]


def test_value_as_is_gives_no_remark():
    result = run("Стол письменный деревянный, столешница 22 мм")

    assert result["remarks"] == []


def test_candidate_scores_sum_to_one():
    candidates = find("письменного стола", REF)

    assert [c["score"] for c in candidates] == [0.5, 0.5]


def test_later_told_value_wins():
    result = run("Стол письменный, Тип каркаса: пластик, Тип каркаса: Деревянный")

    assert result["code"] == "wood"


def test_product_word_before_told_name_is_not_part_of_it():
    result = run("Стол письменный тип каркаса: деревянный")

    char = result["chars"][0]
    assert char["name"] == "Тип каркаса"
    assert char["fragment"] == "тип каркаса: деревянный"


def test_unknown_told_name_is_new_not_similar_ktru_char():
    result = run("Стол письменный, Тип ножек: хром")

    assert result["chars"][0]["source"] == "new"


def test_told_values_do_not_change_candidates():
    plain = find("Стол письменный", REF)
    told = find("Стол письменный, Тип каркаса: Металлический", REF)

    assert told == plain


def test_section_by_name_and_none_when_unknown():
    assert find_section("стол письменный деревянный", PORTAL) == "Столы письменные"
    assert find_section("шины летние", PORTAL) is None
    assert find_section("", PORTAL) is None
    assert find_section("стол письменный", {}) is None


def test_extract_forms_without_reference():
    fragments = extract("Стол деревянный, столешница 2,2 см, высота метр десять, Цвет: белый")

    assert [(f["form"], f["name"], f["text"]) for f in fragments] == [
        ("words", None, "Стол деревянный"),
        ("words", None, "столешница 2,2 см"),
        ("number", "столешница", "2,2 см"),
        ("words", None, "высота метр десять"),
        ("number", "высота", "метр десять"),
        ("pair", "Цвет", "Цвет: белый"),
    ]
    assert (fragments[2]["number"], fragments[2]["unit"]) == (2.2, "004")
    assert (fragments[4]["number"], fragments[4]["unit"]) == (1.1, "006")
    assert fragments[5]["value"] == "белый"


def test_extract_empty_text():
    assert extract("") == []
    assert extract(" , ;") == []


def test_portal_value_when_ktru_has_no_such_char():
    result = run("Стол письменный деревянный, цвет красный, вес 500 г", PORTAL)

    by_name = {c["name"]: c for c in result["chars"]}
    assert by_name["Цвет"]["source"] == "portal"
    assert by_name["Цвет"]["value"] == "Красный"
    assert by_name["Вес"]["value"] == 0.5
    assert by_name["Вес"]["kind"] == "unit"
    assert not by_name["Вес"]["required"]


def test_product_words_and_bare_name_do_not_give_portal_chars():
    result = run("Стол письменный деревянный, размер большой", PORTAL)

    assert {c["name"] for c in result["chars"]} == {"Тип каркаса"}


def test_ktru_wins_over_portal_with_same_name():
    portal: Portal = {
        "Столы письменные": [
            {"name": "Тип каркаса", "type": "Справочник", "unit": None, "values": ["Деревянный"]}
        ]
    }

    result = run("Стол письменный, Тип каркаса: деревянный", portal)

    assert result["chars"][0]["source"] == "ktru"


def test_told_portal_char_keeps_free_value():
    result = run("Стол письменный, Цвет: венге", PORTAL)

    char = result["chars"][0]
    assert (char["source"], char["value"], char["kind"]) == ("portal", "венге", None)
