from typing import Literal

from contracts.eval import ExpectedChar, GoldExample
from contracts.pipeline import FoundChar, Question, Result
from contracts.ref import Position
from pipeline.extract import extract
from scripts.measure import (
    check_chars,
    gold_find,
    gold_link,
    is_right,
    rank,
    report,
    stages_report,
    talk,
)

DESK: Position = {
    "code": "desk-metal",
    "name": "Стол письменный",
    "category": "furniture",
    "okpd2_code": "31.01.10.000",
    "position_type": "final",
    "parent_code": None,
    "units": [],
    "characteristics": [
        {
            "code": "frame",
            "name": "Тип каркаса",
            "type": "text",
            "selection": "fixed",
            "required": True,
            "values": ["Металлический"],
        }
    ],
}
BY_CODE = {"desk-metal": DESK}
QUESTION: Question = {"char": "Тип каркаса", "options": ["Деревянный", "Металлический"]}


def found(name: str, value: str | int | float) -> FoundChar:
    return {
        "name": name,
        "value": value,
        "unit": None,
        "source": "ktru",
        "fragment": name,
        "start": 0,
        "end": 0,
        "required": False,
        "fix": None,
        "kind": None,
    }


def result(code: str | None, question: Question | None = None) -> Result:
    return {
        "category": "furniture",
        "section": None,
        "steps": [],
        "chars": [],
        "code": code,
        "question": question,
        "missing_required": [],
        "remarks": [],
        "candidates": [],
        "options": {},
        "unknown": [],
    }


def gold(text: str, code: str | None, chars: list[ExpectedChar]) -> GoldExample:
    return {"text": text, "expected": {"category": "furniture", "code": code, "chars": chars}}


def no_candidates(_: str) -> list[str]:
    return []


def asks_frame_until_answered(text: str) -> Result:
    if "Металлический" in text:
        return result("desk-metal")
    return result(None, QUESTION)


def test_check_chars_compares_numbers_and_strings_as_text():
    chars = check_chars([{"name": "Размер", "value": "48"}], [found("Размер", 48.0)])

    assert (chars["ok"], chars["wrong"], chars["missed"]) == (1, [], [])


def test_check_chars_separates_wrong_missed_and_extra():
    expected: list[ExpectedChar] = [
        {"name": "Тип каркаса", "value": "Металлический"},
        {"name": "Высота", "value": 750},
    ]

    chars = check_chars(expected, [found("Тип каркаса", "Деревянный"), found("Цвет", "Белый")])

    assert chars["ok"] == 0
    assert chars["found"] == 2
    assert chars["wrong"] == [
        "Тип каркаса: ждали Металлический, получили Деревянный",
        "лишнее Цвет: Белый из «Цвет»",
    ]
    assert chars["missed"] == ["Высота: 750"]


def test_answers_question_with_hidden_code_value():
    example = gold("Стол офисный", "desk-metal", [])

    dialog = talk(example, asks_frame_until_answered, no_candidates, BY_CODE)

    assert is_right(dialog)
    assert dialog["replies"] == ["Тип каркаса: Металлический»"]
    assert dialog["extra_questions"] == 0


def test_question_about_value_from_text_is_extra():
    example = gold(
        "Стол на металлическом каркасе",
        "desk-metal",
        [{"name": "Тип каркаса", "value": "Металлический"}],
    )

    dialog = talk(example, asks_frame_until_answered, no_candidates, BY_CODE)

    assert is_right(dialog)
    assert dialog["extra_questions"] == 1


def test_repeated_question_stops_dialog():
    example = gold("Стол", "desk-metal", [])

    dialog = talk(example, lambda _: result(None, QUESTION), no_candidates, BY_CODE)

    assert dialog["repeated"]
    assert not is_right(dialog)


def test_unknown_product_needs_not_found_not_question():
    example = gold("Рулетка", None, [])

    assert is_right(talk(example, lambda _: result(None), no_candidates, BY_CODE))
    assert not is_right(talk(example, lambda _: result(None, QUESTION), no_candidates, BY_CODE))


def test_report_counts_codes_by_questions():
    dialogs = [
        talk(
            gold("Стол", "desk-metal", []), lambda _: result("desk-metal"), no_candidates, BY_CODE
        ),
        talk(gold("Стол", "desk-metal", []), asks_frame_until_answered, no_candidates, BY_CODE),
        talk(gold("Стол", "desk-metal", []), lambda _: result("other"), no_candidates, BY_CODE),
    ]

    text = report(dialogs)

    assert "Код: 2/3 (67%)" in text
    assert "  сразу: 1" in text
    assert "  после 1 вопроса: 1" in text
    assert "  выбрали неверный код: 1" in text
    assert "ответ: ждали код desk-metal, получили код other" in text


def test_rank_is_position_of_expected_code_or_none():
    assert rank("b", ["a", "b"]) == 2
    assert rank("c", ["a", "b"]) is None
    assert rank(None, ["a"]) is None


def sibling(code: str, position_type: Literal["template", "final"]) -> Position:
    position = DESK.copy()
    position["code"] = code
    position["position_type"] = position_type
    position["parent_code"] = "desk"
    return position


def test_gold_candidates_are_final_codes_of_same_template():
    wood = sibling("desk-wood", "final")
    metal = sibling("desk-metal", "final")
    template = sibling("desk", "template")
    by_code = {"desk-wood": wood, "desk-metal": metal, "desk": template}

    found = gold_find(gold("Стол", "desk-metal", []), by_code)("Стол", [])

    assert [c["code"] for c in found] == ["desk-wood", "desk-metal"]
    assert [c["score"] for c in found] == [0.5, 0.5]


def test_gold_candidates_are_empty_for_product_outside_ktru():
    found = gold_find(gold("Рулетка", None, []), BY_CODE)("Рулетка", [])

    assert found == []


def test_gold_chars_add_values_from_replies_only():
    example = gold("Стол офисный", "desk-metal", [{"name": "Высота", "value": 750}])
    candidates = gold_find(example, BY_CODE)(example["text"], [])
    told = gold_link(example)

    first = told(extract(example["text"]), candidates, None, [DESK], {})
    replied = extract(f"{example['text']}, Тип каркаса: Металлический")
    after_reply = told(replied, candidates, None, [DESK], {})

    assert [(c["name"], c["value"]) for c in first] == [("Высота", 750)]
    assert [(c["name"], c["value"]) for c in after_reply] == [
        ("Высота", 750),
        ("Тип каркаса", "Металлический"),
    ]


def test_stages_report_counts_silently_wrong_and_recall():
    right = talk(
        gold("Стол", "desk-metal", []),
        lambda _: result("desk-metal"),
        lambda _: ["x", "desk-metal"],
        BY_CODE,
    )
    wrong = talk(gold("Стол", "desk-metal", []), lambda _: result("other"), no_candidates, BY_CODE)
    rows = {"как есть": [right, wrong], "эталонные кандидаты": [right], "и характеристики": [right]}

    text = stages_report(rows)

    assert "Recall@1 0/2 (0%), Recall@5 1/2 (50%), Recall@20 1/2 (50%), MRR 0.25" in text
    assert "как есть: верно 1/2 (50%), молча неверно 1, вопросов 0.0" in text


def test_stages_report_on_empty_gold():
    text = stages_report({"как есть": [], "эталонные кандидаты": [], "и характеристики": []})

    assert "Recall@1 0/0" in text
    assert "верно 0/0, молча неверно 0" in text
