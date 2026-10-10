from contracts.ref import Characteristic, Position
from scripts.generate import generate, section_texts
from scripts.stages import f_score, split, split_pairs, stage1, stage2, stage4, stage5

FRAME: Characteristic = {
    "code": "frame",
    "name": "Тип каркаса",
    "type": "text",
    "selection": "fixed",
    "required": True,
    "values": ["Деревянный"],
}
TOP: Characteristic = {
    "code": "top",
    "name": "Толщина материала столешницы",
    "type": "number",
    "selection": "fixed",
    "required": False,
    "values": [{"min": 16, "min_inclusive": True, "max": 25, "max_inclusive": False}],
    "unit": {"code": "003", "name": "Миллиметр"},
}
DESK: Position = {
    "code": "wood",
    "name": "Стол письменный",
    "category": "furniture",
    "okpd2_code": "31.01.10.000",
    "position_type": "final",
    "parent_code": None,
    "units": [],
    "characteristics": [FRAME, TOP],
}
REF = [DESK]
BY_CODE = {"wood": DESK}


def test_generated_fragments_point_into_text():
    for seed in range(20):
        for example in generate(REF, 1, seed):
            assert example["code"] == "wood"
            for item in example["fragments"]:
                assert example["text"][item["start"] : item["end"]] == item["text"]
            assert {c["name"] for c in example["chars"]} == {FRAME["name"], TOP["name"]}


def test_generated_number_is_inside_ktru_range():
    for seed in range(20):
        example = generate(REF, 1, seed)[0]
        top = [c for c in example["chars"] if c["name"] == TOP["name"]][0]
        assert 16 <= float(top["value"]) < 25


def test_generate_is_repeatable_and_handles_empty_reference():
    assert generate(REF, 1, 7) == generate(REF, 1, 7)
    assert generate([], 5) == []
    assert section_texts({}, 5) == []


def test_split_pairs_by_longest_known_name():
    names = {"высота", "высота шкафа", "масса"}

    pairs = split_pairs("Высота шкафа до 90 см | Масса 26 г | Непонятно что", names)

    assert pairs == [("Высота шкафа", "до 90 см"), ("Масса", "26 г")]
    assert split_pairs("", names) == []


def test_stage_lines_on_tiny_reference():
    generated = generate(REF, 1)

    assert "в топ-5 1/1" in stage1(generated, REF)
    assert "лишних 0" in stage4(generated, BY_CODE, REF)
    assert "со всеми значениями код верен 1/1" in stage5(generated, BY_CODE, REF)
    assert "0/0" in stage1([], REF)


def test_f_score_weights_precision_and_survives_zero():
    assert f_score(0, 0, 0, 0.5) == 0
    assert f_score(1, 1, 1, 0.5) == 1
    precise = f_score(5, 5, 10, 0.5)
    complete = f_score(5, 10, 5, 0.5)
    assert precise > complete


def test_generated_split_into_clean_and_spoiled():
    generated = []
    for seed in range(20):
        generated += generate(REF, 1, seed)

    (_, clean), (_, spoiled) = split(generated)

    assert clean and spoiled
    assert len(clean) + len(spoiled) == len(generated)
    for example in clean:
        assert example["text"].startswith(DESK["name"].lower())
        assert all("kind" not in char for char in example["chars"])


def test_stage2_counts_refusal_apart_from_error():
    portal = {"Столы письменные": []}

    line = stage2([("стол письменный", "Столы письменные"), ("", "Столы письменные")], portal)

    assert "отказ 1/2" in line
