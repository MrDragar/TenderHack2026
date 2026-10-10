"""Проверка синтетики: отличает ли она слабое решение от сильного так же, как ручной эталон.

  uv run python -m scripts.calibrate

Ступени это бейзлайн, у которого отключено по одной способности. Синтетика годится, если
ступени на ней идут в том же порядке, что на живых данных: на золоте, а для раздела
портала на листе 1 с разделом по совпадению характеристик, бейзлайн не упирается в потолок,
а разрыв между ступенями не меньше, чем на золоте. Синтетика почти всегда легче живых
текстов, поэтому важен порядок и разрыв, а не сами проценты.
"""

from collections.abc import Callable
from contextlib import ExitStack
from itertools import combinations
from typing import Any
from unittest.mock import patch

from contracts.eval import ExpectedChar, GoldExample
from contracts.pipeline import FoundChar
from contracts.ref import Position
from pipeline.candidates import find
from pipeline.link import link
from pipeline.run import portal, reference, solve
from pipeline.section import find_section
from pipeline.values import same
from scripts.generate import Generated, generate, section_texts, siblings
from scripts.measure import check_chars, gold_find, load_gold
from scripts.products import products
from scripts.stages import GENERATED, SECTIONS, named, template_names, template_of

# Тег у ступени показывает только синтетика: на 60 фразах золота разбивка по тегам шумная.
SHOWN_TAGS = 12

type Patches = list[tuple[str, Any]]


def exact(word: str) -> str:
    return word.lower().replace("ё", "е")


def unchanged(found: Any, *_: Any) -> Any:
    return found


def nothing(*_: Any, **__: Any) -> list:
    return []


NO_TYPOS: Patches = [
    ("pipeline.text.correct", unchanged),
    ("pipeline.link.correct", unchanged),
    ("pipeline.link.get_close_matches", nothing),
]
NO_UNITS: Patches = [
    *NO_TYPOS,
    ("pipeline.link.convert", unchanged),
    ("pipeline.link.check_range", nothing),
]
BY_NAME: dict[str, Patches] = {
    "точные слова": [*NO_TYPOS, ("pipeline.text.stem", exact)],
    "без исправления опечаток": NO_TYPOS,
    "бейзлайн": [],
}
RUNGS: dict[str, dict[str, Patches]] = {
    "candidates": BY_NAME,
    "section": BY_NAME,
    "link": {
        "точные слова": [*NO_UNITS, ("pipeline.text.stem", exact)],
        "без опечаток и единиц": NO_UNITS,
        "без исправления опечаток": NO_TYPOS,
        "бейзлайн": [],
    },
}


def patched[T](patches: Patches, work: Callable[[], T]) -> T:
    with ExitStack() as stack:
        for target, new in patches:
            stack.enter_context(patch(target, new))
        return work()


def synthetic_candidates(generated: list[Generated], ref: list[Position]) -> list[bool]:
    by_code = {p["code"]: p for p in ref}
    return [first_right(e["text"], e["code"], ref, by_code) for e in generated]


def gold_candidates(gold: list[GoldExample], ref: list[Position]) -> list[bool]:
    by_code = {p["code"]: p for p in ref}
    found = []
    for example in gold:
        code = example["expected"]["code"]
        if code:
            found.append(first_right(example["text"], code, ref, by_code))
    return found


def first_right(text: str, code: str, ref: list[Position], by_code: dict[str, Position]) -> bool:
    top = template_names(find(text, ref), by_code)
    return bool(top) and top[0] == named(template_of(code, by_code), by_code)


def synthetic_link(generated: list[Generated], ref: list[Position]) -> list[bool]:
    """Пример верен, если все значения и виды правок совпали и нет лишних."""
    by_code = {p["code"]: p for p in ref}
    found = []
    for example in generated:
        candidates = siblings(by_code[example["code"]], by_code)
        chars = link(example["fragments"], candidates, None, ref, {})
        right = all(told(char, chars) for char in example["chars"])
        names = {char["name"] for char in example["chars"]}
        found.append(right and all(char["name"] in names for char in chars))
    return found


def told(expected: ExpectedChar, chars: list[FoundChar]) -> bool:
    for char in chars:
        if char["name"] == expected["name"] and same(char["value"], expected["value"]):
            if char["kind"] == expected.get("kind"):
                return True
    return False


def gold_link(gold: list[GoldExample], ref: list[Position]) -> list[bool]:
    """На золоте нет кусков текста, поэтому привязка идёт после настоящего извлечения."""
    by_code = {p["code"]: p for p in ref}
    sections = portal()
    found = []
    for example in gold:
        if example["expected"]["code"]:
            result = solve(example["text"], ref, sections, find=gold_find(example, by_code))
            chars = check_chars(example["expected"]["chars"], result["chars"])
            found.append(not chars["wrong"] and not chars["missed"])
    return found


def synthetic_section(_: list[Generated], __: list[Position]) -> list[bool]:
    sections = portal()
    found = []
    for text, section in section_texts(sections, SECTIONS):
        found.append(find_section(text, sections) == section)
    return found


def sheet1_section(_: list[GoldExample], __: list[Position]) -> list[bool]:
    """Только название товара: раздел взят из названий его характеристик."""
    sections = portal()
    found = []
    for product in products(sections):
        if product["section"]:
            found.append(find_section(product["name"], sections) == product["section"])
    return found


MEASURES = {
    "candidates": ("шаблон первым, живые это золото", synthetic_candidates, gold_candidates),
    "link": ("пример верен целиком, живые это золото", synthetic_link, gold_link),
    "section": ("раздел угадан, живые это лист 1", synthetic_section, sheet1_section),
}


def share(found: list[bool]) -> float:
    return sum(found) / len(found) if found else 0.0


def agreement(synthetic: list[float], gold: list[float]) -> str:
    """Пары ступеней, которые синтетика упорядочила как живые данные (как тау Кендалла).
    Ничьи на живых не считаем: на 55 фразах золота одна фраза это почти два процента."""
    same_order = 0
    compared = 0
    for i, j in combinations(range(len(gold)), 2):
        if gold[i] != gold[j]:
            compared += 1
            same_order += (synthetic[i] - synthetic[j]) * (gold[i] - gold[j]) > 0
    return f"{same_order} из {compared} пар, различимых на живых"


def by_tag(generated: list[Generated], weak: list[bool], strong: list[bool]) -> list[str]:
    """Тег без разрыва между слабой и сильной ступенью ничего не различает."""
    tags: dict[str, list[int]] = {}
    for example, low, high in zip(generated, weak, strong, strict=True):
        for tag in example["tags"]:
            counts = tags.setdefault(tag, [0, 0, 0])
            counts[0] += low
            counts[1] += high
            counts[2] += 1
    lines = []
    ranked = sorted(tags.items(), key=lambda item: (item[1][0] - item[1][1]) / item[1][2])
    for tag, (low, high, total) in ranked[:SHOWN_TAGS]:
        lines.append(f"    {tag}: {low / total:.0%} → {high / total:.0%} из {total}")
    return lines


def report(stage: str, generated: list[Generated], gold: list[GoldExample]) -> list[str]:
    ref = reference()
    title, on_synthetic, on_gold = MEASURES[stage]
    lines = [f"{stage}: {title}", f"  {'':28}{'синтетика':>10}{'живые':>9}"]
    synthetic_rows = []
    synthetic_scores = []
    gold_scores = []
    for rung, patches in RUNGS[stage].items():
        found = patched(patches, lambda: on_synthetic(generated, ref))
        synthetic_rows.append(found)
        synthetic_scores.append(share(found))
        gold_scores.append(share(patched(patches, lambda: on_gold(gold, ref))))
        lines.append(f"  {rung:28}{synthetic_scores[-1]:>10.0%}{gold_scores[-1]:>9.0%}")
    order = agreement(synthetic_scores, gold_scores)
    synthetic_gap = synthetic_scores[-1] - synthetic_scores[0]
    gold_gap = gold_scores[-1] - gold_scores[0]
    easy = share(synthetic_rows[0])
    lines += [
        f"  порядок ступеней совпал: {order}",
        f"  разрыв слабая → бейзлайн: синтетика {synthetic_gap:+.0%}, живые {gold_gap:+.0%}",
        f"  решает даже слабая ступень: {easy:.0%} синтетики",
    ]
    # У текстов раздела нет тегов генератора.
    if stage != "section":
        lines.append("  по тегам, слабая → бейзлайн, сверху самые различающие:")
        lines += by_tag(generated, synthetic_rows[0], synthetic_rows[-1])
    return lines


if __name__ == "__main__":
    generated = generate(reference(), GENERATED)
    gold = load_gold()
    for stage in MEASURES:
        print("\n".join(report(stage, generated, gold)))
        print()
