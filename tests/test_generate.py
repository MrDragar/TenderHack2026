from random import Random

from contracts.ref import Characteristic, Position
from pipeline.link import SHIFTS
from scripts.generate import (
    answerable,
    flag_piece,
    inflected,
    name_piece,
    size_pair,
    spoil_number,
    typo_word,
)


def char(name: str, values: list, kind: str = "text", unit: str | None = None) -> Characteristic:
    found: Characteristic = {
        "code": name,
        "name": name,
        "type": "number" if kind == "number" else "text",
        "selection": "single",
        "required": False,
        "values": values,
    }
    if unit:
        found["unit"] = {"code": unit, "name": unit}
    return found


def position(code: str, name: str, chars: list[Characteristic]) -> Position:
    return {
        "code": code,
        "name": name,
        "category": "furniture",
        "okpd2_code": "31.01.10.000",
        "position_type": "final",
        "parent_code": None,
        "units": [],
        "characteristics": chars,
    }


def test_value_without_name_only_when_one_characteristic_has_it():
    frame = char("Материал каркаса", ["Металл", "Дерево"])
    top = char("Материал столешницы", ["Металл", "Стекло"])
    size = char("Российский размер", ["70"])
    family = [position("1", "Стол письменный", [frame, top, size])]

    assert answerable("Дерево", frame, family)
    assert not answerable("Металл", frame, family)
    assert not answerable("70", size, family)


def test_short_name_stays_unique_and_whole():
    names = {"a": "Стол письменный", "b": "Стол обеденный", "c": "Шкаф для одежды"}

    for seed in range(50):
        desk = name_piece("Стол письменный", "a", names, Random(seed))
        if not any("в названии" in tag for tag in desk["tags"]):
            assert "письменн" in desk["text"].lower()
        shelf = name_piece("Шкаф для одежды", "c", names, Random(seed))
        assert shelf["text"].lower().split()[-1] not in ("для", "из", "с")


def test_spoiled_number_is_recoverable_or_untouched():
    for seed in range(50):
        number, unit, kind = spoil_number(20, "003", (16, 25), Random(seed))
        if kind == "wrong_unit":
            fits = [s for s in SHIFTS if 16 <= number * s <= 25]
            assert len(fits) == 1 and round(number * fits[0], 6) == 20
        if kind == "out_of_range":
            assert not any(16 <= number * s <= 25 for s in SHIFTS)
        if kind is None:
            assert (number, unit) == (20, "003")


def test_typo_keeps_first_letter_and_changes_word():
    for how in ("vowel", "drop", "double", "swap", "key"):
        for seed in range(10):
            spoiled = typo_word("деревянный", how, Random(seed))
            assert spoiled != "деревянный"
            assert spoiled[0] == "д"


def test_flag_no_is_written_with_without():
    flag = char("Наличие подсветки", ["Да", "Нет"])

    written = {flag_piece(flag, "Нет", Random(seed))["text"] for seed in range(30)}

    assert "без подсветки" in written
    assert all("подсветк" in text.lower() for text in written)


def test_inflected_keeps_meaning_of_value():
    rng = Random(0)
    assert inflected("Деревянный", False, rng)[:-2] == "Деревянн"
    assert inflected("Чугун", True, rng) == "из Чугуна"
    assert inflected("Сталь", True, rng) == "из Стали"
    assert inflected("ЛДСП", True, rng) == "из ЛДСП"
    assert inflected("Чугун", False, rng) == "Чугун"
    assert inflected("Мягкое сиденье", False, rng) == "Мягкое сиденье"


def test_sizes_only_length_and_width_of_one_thing():
    length = char("Длина столешницы", [1200], "number", "003")
    width = char("Ширина столешницы", [800], "number", "003")
    height = char("Высота", [750], "number", "003")
    shelf = char("Ширина полки", [300], "number", "003")

    assert size_pair([height, width, length]) == (length, width)
    assert size_pair([length, shelf, height]) is None
    assert size_pair([]) is None
