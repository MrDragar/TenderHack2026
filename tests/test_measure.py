from contracts.eval import ExpectedChar, GoldExample
from contracts.pipeline import FoundChar, Result
from scripts.measure import check, check_chars, report


def found(name: str, value: str | int | float) -> FoundChar:
    return {"name": name, "value": value, "unit": None, "source": "ktru", "fragment": ""}


def result(code: str | None, chars: list[FoundChar]) -> Result:
    return {
        "category": "furniture",
        "steps": [],
        "chars": chars,
        "code": code,
        "question": None,
        "missing_required": [],
        "remarks": [],
    }


EXAMPLE: GoldExample = {
    "text": "Стол письменный металлический",
    "expected": {
        "category": "furniture",
        "code": "31.01.10.000-00000005",
        "chars": [{"name": "Тип каркаса", "value": "Металлический"}],
    },
}


def test_check_counts_correct_answer():
    checked = check(
        EXAMPLE, lambda _: result("31.01.10.000-00000005", [found("Тип каркаса", "Металлический")])
    )

    assert checked["category_ok"] and checked["code_ok"]
    assert checked["chars_ok"] == 1


def test_check_chars_compares_numbers_and_strings_as_text():
    ok, wrong, missed = check_chars([{"name": "Размер", "value": "48"}], [found("Размер", 48.0)])

    assert (ok, wrong, missed) == (1, [], [])


def test_check_chars_separates_wrong_and_missed():
    expected: list[ExpectedChar] = [
        {"name": "Тип каркаса", "value": "Металлический"},
        {"name": "Высота", "value": 750},
    ]

    ok, wrong, missed = check_chars(expected, [found("Тип каркаса", "Деревянный")])

    assert ok == 0
    assert wrong == ["Тип каркаса: ждали Металлический, получили Деревянный"]
    assert missed == ["Высота: 750"]


def test_question_instead_of_code_is_an_error():
    checked = check(EXAMPLE, lambda _: result(None, []))

    text = report([checked])

    assert "Код: 0/1 (0%)" in text
    assert "код: ждали 31.01.10.000-00000005, получили None" in text


def test_no_code_expected_and_none_returned_is_correct():
    example: GoldExample = {
        "text": "Стол офисный",
        "expected": {"category": "furniture", "code": None, "chars": []},
    }

    checked = check(example, lambda _: result(None, []))

    assert checked["code_ok"]
    assert "«Стол офисный»" not in report([checked])
