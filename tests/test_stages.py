from contracts.ref import Characteristic, Position
from scripts.generate import generate, section_texts
from scripts.products import Product, split_pairs, tags, weak_section
from scripts.stages import (
    Stage,
    by_tag,
    compare,
    covering,
    f_score,
    glued,
    split,
    stage1,
    stage2,
    stage2_products,
    stage4,
    stage5,
    stage34,
)

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
    for seed in range(40):
        for example in generate(REF, 1, seed):
            assert example["code"] == "wood"
            for item in example["fragments"]:
                assert example["text"][item["start"] : item["end"]] == item["text"]
            assert example["chars"]
            assert {c["name"] for c in example["chars"]} <= {FRAME["name"], TOP["name"]}


def test_generated_number_is_inside_ktru_range():
    for seed in range(40):
        for item in generate(REF, 1, seed)[0]["chars"]:
            if item["name"] == TOP["name"] and "kind" not in item:
                assert item["value"] == "≥ 16 < 25" or 16 <= float(item["value"]) < 25


def test_generate_is_repeatable_and_handles_empty_reference():
    assert generate(REF, 1, 7) == generate(REF, 1, 7)
    assert generate([], 5) == []
    assert section_texts({}, 5) == []


def test_split_pairs_by_longest_known_name():
    names = {"высота", "высота шкафа", "масса"}

    pairs = split_pairs("Высота шкафа до 90 см | Масса 26 г | Непонятно что", names)

    assert pairs == [("Высота шкафа", "до 90 см"), ("Масса", "26 г")]
    assert split_pairs("", names) == []
    assert split_pairs("Масса  26   г", names) == [("Масса", "26 г")]


def test_weak_section_only_when_one_section_has_every_name():
    sections = {
        "Шкафы": {"высота шкафа", "масса", "цвет"},
        "Сейфы": {"масса", "тип замка"},
        "Стулья": {"масса", "цвет"},
    }

    assert weak_section([("Высота шкафа", "90 см"), ("Масса", "26 г")], sections) == "Шкафы"
    assert weak_section([("Масса", "26 г")], sections) is None
    assert weak_section([("Масса", "1 кг"), ("Ремень", "есть")], sections) is None
    assert weak_section([], {"Шкафы": set()}) == "Шкафы"


def test_tags_mark_long_names_and_models():
    assert tags("Шкаф КБС-011Т") == ["бренд или модель"]
    assert tags("Перфоратор Makita HR2450FT") == ["бренд или модель"]
    assert tags("Шкаф ПРАКТИК для документов") == ["бренд или модель"]
    assert tags("Стол письменный 1200х800") == []
    assert "длинное название" in tags("Стол " * 10)
    assert tags("") == []


def product(name: str, pairs: list[tuple[str, str]], section: str | None) -> Product:
    return {
        "id": "1",
        "category": "furniture",
        "name": name,
        "pairs": pairs,
        "section": section,
        "tags": [],
        "test": False,
    }


def test_glued_spans_point_at_pairs():
    item = product("Шкаф", [("Масса", "26 г"), ("Цвет", "серый")], None)

    text, spans = glued(item)

    assert text == "Шкаф, Масса 26 г, Цвет серый"
    assert [text[a:b] for a, b in spans] == ["Масса 26 г", "Цвет серый"]
    assert glued(product("Шкаф", [], None)) == ("Шкаф", [])


def test_covering_finds_char_inside_pair_span():
    char = {"start": 12, "source": "ktru"}

    assert covering([char], (6, 16)) is char
    assert covering([char], (17, 27)) is None
    assert covering([], (0, 5)) is None


def test_stage2_on_products_skips_unlabeled_and_slices_by_category():
    portal = {"Столы письменные": [], "Шкафы": []}
    items = [
        product("стол письменный", [], "Столы письменные"),
        product("шкаф", [], "Шкафы"),
        product("что-то", [], None),
    ]

    metrics = stage2_products(items, portal)["metrics"]

    assert metrics["с меткой раздела"] == (2, 3)
    assert metrics["угадан"] == (2, 2)
    assert metrics["угадан, мебель"] == (2, 2)
    assert stage2_products([], portal)["metrics"]["угадан"] == (0, 0)


def test_stage_metrics_on_tiny_reference():
    generated = generate(REF, 1)

    top5 = stage1(generated, REF)["metrics"]
    assert (1, 1) in (top5["чистые: шаблон в топ-5"], top5["с порчей: шаблон в топ-5"])
    assert stage4(generated, BY_CODE, REF)["metrics"]["лишних"] == 0
    narrowed = stage5(generated, BY_CODE, REF)
    assert narrowed["metrics"]["со всеми значениями код верен"] == (1, 1)
    assert narrowed["failures"] == []
    empty = stage1([], REF)
    assert empty["metrics"]["чистые: шаблон в топ-1"] == (0, 0)
    assert empty["failures"] == []


def test_stage34_counts_whole_examples_and_tags():
    generated = generate(REF, 1)

    metrics = stage34(generated, BY_CODE, REF, {})["metrics"]

    assert metrics["пример верен целиком"] in ((0, 1), (1, 1))
    assert stage34([], BY_CODE, REF, {})["metrics"]["пример верен целиком"] == (0, 0)


def test_by_tag_counts_each_tag_apart():
    first = generate(REF, 1, 0)[0]
    second = first.copy()
    first["tags"], second["tags"] = ["пара"], ["пара", "диапазон"]

    metrics = by_tag([first, second], [True, False], "верно")

    assert metrics == {"верно, диапазон": (0, 1), "верно, пара": (1, 2)}


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
        assert "опечатка в названии" not in example["tags"]
        assert all("kind" not in char for char in example["chars"])


def test_stage2_counts_refusal_apart_from_error():
    portal = {"Столы письменные": []}

    stage = stage2([("стол письменный", "Столы письменные"), ("", "Столы письменные")], portal)

    assert stage["metrics"]["отказ"] == (1, 2)
    assert stage["failures"] == [{"text": "", "expected": "Столы письменные", "got": "отказ"}]


def stage(right: int, failures: list[str]) -> Stage:
    return {
        "key": "stage2",
        "title": "2. Раздел портала",
        "metrics": {"угадан": (right, 3)},
        "failures": [{"text": text, "expected": "стол", "got": "отказ"} for text in failures],
    }


def test_compare_shows_new_failures_since_last_run(tmp_path):
    first = compare(stage(2, ["тумба"]), tmp_path)
    second = compare(stage(1, ["шкаф", "кресло"]), tmp_path)

    assert "было" not in first
    assert "провалов 1" in first
    assert "угадан: 1/3 (33%) (было 2/3 (67%))" in second
    assert "ушло провалов 1, новых 2" in second
    assert "«шкаф»: ждали стол, получили отказ" in second
