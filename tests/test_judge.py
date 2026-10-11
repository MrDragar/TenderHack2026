from contracts.ref import Position
from pipeline.section import find_section
from scripts.generate import generate, held_out
from scripts.judge import Item, cost, counts, item, load, pairs, report, section_items


def desk(code: str) -> Position:
    return {
        "code": code,
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
                "values": ["Деревянный"],
            }
        ],
    }


def test_test_part_shares_no_positions_with_dev():
    ref = [desk(str(n)) for n in range(40)]

    dev = {e["code"] for e in generate(ref, 40)}
    test = {e["code"] for e in generate(ref, 40, test=True)}

    assert dev and test
    assert not dev & test
    assert all(held_out(code) for code in test)


def test_duplicate_keys_pair_in_order():
    base = [item("стол", "a", "a", "верно"), item("стол", "b", "x", "неверно")]
    variant = [item("стол", "a", "a", "верно"), item("стол", "b", "b", "верно")]

    found = pairs(base, variant)

    assert [(p["base"], p["variant"]) for p in found] == [("верно", "верно"), ("неверно", "верно")]


def test_extra_on_one_side_means_other_side_gave_none():
    extra: Item = item("стол | лишнее Цвет", "ничего", "Цвет: красный", "лишнее")

    found = pairs([extra], [])

    assert (found[0]["base"], found[0]["variant"], found[0]["variant_got"]) == (
        "лишнее",
        "нет",
        "ничего",
    )
    assert pairs([], []) == []


def test_variant_extras_do_not_change_base_cost():
    gold = [item("стол | Цвет", "белый", "белый", "верно"), item("стол | Тип", "a", "", "отказ")]
    extras = [item(f"стол | лишнее {n}", "ничего", "x", "лишнее") for n in range(5)]

    clean = [p["base"] for p in pairs(gold, gold)]
    noisy = pairs(gold, gold + extras)

    assert cost(counts([p["base"] for p in noisy])) == cost(counts(clean)) == 0.5
    assert cost(counts([p["variant"] for p in noisy])) == (1 + 5 * 2) / 2


def test_wrong_costs_twice_a_refusal():
    assert cost(counts(["верно", "неверно"])) == 1
    assert cost(counts(["отказ"])) == 1
    assert cost(counts([])) == 0
    assert cost(counts(["лишнее"])) == 0


def test_base_against_itself_has_no_transitions():
    portal = {"Столы письменные": [], "Стулья": []}
    base = section_items(load("section", "base"), [], portal, False)

    lines = report("section", "base", pairs(base, base))

    assert load("section", "base") is find_section
    assert "Стало лучше 0, хуже 0" in lines
