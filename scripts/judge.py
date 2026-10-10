"""Судья: меряет вариант этапа рядом с бейзлайном на одних входах и пишет прогон в журнал.

  uv run python -m scripts.judge link synonyms          # dev: переходы, примеры, журнал
  uv run python -m scripts.judge link synonyms --test   # отложенная часть, запускает человек
Вариант это variants/link/synonyms.py с функцией link той же сигнатуры, что в contracts.
Вариант base это сам бейзлайн из pipeline/. Как работать с вариантами: docs/improving.md.
"""

import importlib
import inspect
import json
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from random import Random
from typing import Any, TypedDict

from contracts.pipeline import Candidate, FoundChar
from contracts.ref import Portal, Position
from pipeline.run import portal, reference
from pipeline.values import same, show
from scripts.generate import generate, section_texts, siblings
from scripts.products import RAW_DIR, products
from scripts.stages import (
    GENERATED,
    SECTIONS,
    described,
    fixed_chars,
    glued,
    same_pair,
    template_of,
    templates,
)

# Этап и имя функции, которую вариант подменяет: в pipeline/<этап>.py она называется так же.
FUNCTIONS = {
    "candidates": "find",
    "section": "find_section",
    "extract": "extract",
    "link": "link",
    "narrow": "narrow",
}
# Молча неверный ответ хуже честного «не знаю»: вопрос стоит человеку одного действия.
COST = {"верно": 0, "отказ": 1, "неверно": 2}
SHOWN = 5
# Ladder: на test число показываем, только если цена упала заметно, иначе подгоним под test.
LADDER_STEP = 0.01
DEV_DIR = Path("data/eval")
TEST_DIR = Path("data/eval/test")


type Stage = Callable[..., Any]


class Item(TypedDict):
    key: str
    expected: str
    got: str
    outcome: str


class Pair(TypedDict):
    key: str
    expected: str
    base: str
    variant: str
    base_got: str
    variant_got: str


def load(stage: str, idea: str) -> Stage:
    module = f"pipeline.{stage}" if idea == "base" else f"variants.{stage}.{idea}"
    return getattr(importlib.import_module(module), FUNCTIONS[stage])


def hypothesis(stage: str, idea: str) -> str:
    """Первая строка докстринга варианта: её видно в журнале рядом с числами."""
    if idea == "base":
        return "бейзлайн"
    doc = inspect.getdoc(importlib.import_module(f"variants.{stage}.{idea}")) or ""
    return doc.splitlines()[0] if doc else "гипотеза не записана"


def item(key: str, expected: str, got: str, outcome: str) -> Item:
    return {"key": key, "expected": expected, "got": got, "outcome": outcome}


def candidates_items(find: Stage, ref: list[Position], _: Portal, test: bool) -> list[Item]:
    """Верно, если шаблон загаданной позиции первый: соседей одного шаблона различает сужение."""
    by_code = {p["code"]: p for p in ref}
    items = []
    for example in generate(ref, GENERATED, test=test):
        found = templates(find(example["text"], ref), by_code)
        gold = template_of(example["code"], by_code)
        outcome = "отказ"
        if found:
            outcome = "верно" if found[0] == gold else "неверно"
        got = "; ".join(found[:3]) or "ничего"
        items.append(item(example["text"], gold, got, outcome))
    return items


def section_items(
    find_section: Stage, _: list[Position], sections: Portal, test: bool
) -> list[Item]:
    """Синтетика и названия товаров листа 1 с разделом по совпадению характеристик."""
    texts = []
    for text, section in section_texts(sections, SECTIONS, test=test):
        texts.append((text, text, section))
    for product in products(sections, test=test):
        if product["section"]:
            texts.append((f"лист 1: {product['name']}", product["name"], product["section"]))
    items = []
    for key, text, section in texts:
        found = find_section(text, sections)
        outcome = "отказ"
        if found is not None:
            outcome = "верно" if found == section else "неверно"
        items.append(item(key, section, found or "отказ", outcome))
    return items


def extract_items(extract: Stage, _: list[Position], sections: Portal, test: bool) -> list[Item]:
    """Пара с другим значением под тем же названием неверна, ненайденная пара это пропуск."""
    found = products(sections, test=test)
    if not found:
        sys.exit(f"Нет листа 1 в {RAW_DIR}, этап extract не измерить")
    items = []
    for product in found:
        text = glued(product)[0]
        fragments = extract(text)
        for pair_name, value in product["pairs"]:
            same_name = []
            for fragment in fragments:
                if (fragment["name"] or "").casefold() == pair_name.casefold():
                    same_name.append(fragment)
            outcome = "отказ"
            if same_name:
                outcome = "неверно"
            if any(same_pair(f, pair_name, value) for f in same_name):
                outcome = "верно"
            got = "; ".join(f["value"] for f in same_name) or "ничего"
            items.append(item(f"{text} | {pair_name}", value, got, outcome))
    return items


def link_items(link: Stage, ref: list[Position], _: Portal, test: bool) -> list[Item]:
    """Верно, только если и значение, и вид правки совпали: ложная правка видна пользователю.
    Лишняя характеристика тоже ошибка, у неё свой ключ."""
    by_code = {p["code"]: p for p in ref}
    items = []
    for example in generate(ref, GENERATED, test=test):
        candidates = siblings(by_code[example["code"]], by_code)
        found: list[FoundChar] = link(example["fragments"], candidates, None, ref, {})
        names = set()
        for char in example["chars"]:
            names.add(char["name"])
            mine = [f for f in found if f["name"] == char["name"]]
            outcome = "отказ"
            if mine:
                outcome = "неверно"
                for f in mine:
                    if same(f["value"], char["value"]) and f["kind"] == char.get("kind"):
                        outcome = "верно"
            expected = described(char["name"], char["value"], char.get("kind"))
            got = "; ".join(described(f["name"], f["value"], f["kind"], f["rule"]) for f in mine)
            items.append(
                item(f"{example['text']} | {char['name']}", expected, got or "ничего", outcome)
            )
        for f in found:
            if f["name"] not in names:
                got = described(f["name"], f["value"], f["kind"], f["rule"])
                items.append(
                    item(f"{example['text']} | лишнее {f['name']}", "ничего", got, "неверно")
                )
    return items


def narrow_items(narrow: Stage, ref: list[Position], _: Portal, test: bool) -> list[Item]:
    """Со всеми неизменяемыми значениями и без одного: без него честный ответ это вопрос."""
    by_code = {p["code"]: p for p in ref}
    rng = Random(0)
    items = []
    for example in generate(ref, GENERATED, test=test):
        position = by_code[example["code"]]
        candidates = siblings(position, by_code)
        chars = fixed_chars(position)
        items.append(narrow_item(narrow, candidates, chars, ref, example["code"], "все значения"))
        if chars:
            chars.pop(rng.randrange(len(chars)))
            items.append(narrow_item(narrow, candidates, chars, ref, example["code"], "без одного"))
    return items


def narrow_item(
    narrow: Stage,
    candidates: list[Candidate],
    chars: list[FoundChar],
    ref: list[Position],
    code: str,
    label: str,
) -> Item:
    result = narrow(candidates, chars, ref)
    values = ", ".join(f"{c['name']}: {show(c['value'])}" for c in chars)
    key = f"{position_name(ref, code)} {label}: {values}"
    if result["code"] is None:
        question = result["question"]["char"] if result["question"] else "нет кода"
        return item(key, code, f"вопрос про {question}", "отказ")
    outcome = "верно" if result["code"] == code else "неверно"
    return item(key, code, result["code"], outcome)


def position_name(ref: list[Position], code: str) -> str:
    for position in ref:
        if position["code"] == code:
            return position["name"]
    return code


BUILDERS = {
    "candidates": candidates_items,
    "section": section_items,
    "extract": extract_items,
    "link": link_items,
    "narrow": narrow_items,
}


def pairs(base: list[Item], variant: list[Item]) -> list[Pair]:
    by_key = numbered(variant)
    found = []
    for key, one in numbered(base).items():
        found.append(joined(one, by_key.pop(key, None)))
    for one in by_key.values():
        found.append(joined(None, one))
    return found


def numbered(items: list[Item]) -> dict[tuple[str, int], Item]:
    """У разных позиций бывает одно название и одни значения: повторы сводим по порядку."""
    seen: dict[str, int] = {}
    found = {}
    for one in items:
        seen[one["key"]] = seen.get(one["key"], 0) + 1
        found[(one["key"], seen[one["key"]])] = one
    return found


def joined(base: Item | None, variant: Item | None) -> Pair:
    """Ключа нет у одной из сторон, если только она дала лишнее: значит, другая не ошиблась."""
    known = base or variant
    assert known is not None
    return {
        "key": known["key"],
        "expected": known["expected"],
        "base": base["outcome"] if base else "верно",
        "variant": variant["outcome"] if variant else "верно",
        "base_got": base["got"] if base else "ничего",
        "variant_got": variant["got"] if variant else "ничего",
    }


def counts(outcomes: list[str]) -> dict[str, int]:
    result = {outcome: 0 for outcome in COST}
    for outcome in outcomes:
        result[outcome] += 1
    return result


def cost(side: dict[str, int]) -> float:
    total = sum(side.values())
    return sum(COST[o] * n for o, n in side.items()) / total if total else 0.0


def report(stage: str, idea: str, found: list[Pair]) -> list[str]:
    base = counts([p["base"] for p in found])
    variant = counts([p["variant"] for p in found])
    lines = [
        f"{stage} {idea}: {hypothesis(stage, idea)}",
        f"{'':10}{'верно':>8}{'отказ':>8}{'неверно':>9}{'цена':>7}",
        row("бейзлайн", base),
        row("вариант", variant),
        "",
        "Переходы: строка это бейзлайн, столбец это вариант",
        f"{'':10}{'верно':>8}{'отказ':>8}{'неверно':>9}",
    ]
    for before in COST:
        cells = []
        for after in COST:
            cells.append(sum(p["base"] == before and p["variant"] == after for p in found))
        lines.append(f"{before:10}{cells[0]:>8}{cells[1]:>8}{cells[2]:>9}")
    better = sum(COST[p["variant"]] < COST[p["base"]] for p in found)
    worse = sum(COST[p["variant"]] > COST[p["base"]] for p in found)
    lines += ["", f"Стало лучше {better}, хуже {worse}"]
    # Сначала самое дорогое: честный ответ стал уверенной ошибкой.
    for before, after in (("отказ", "неверно"), ("верно", "неверно"), ("верно", "отказ")):
        lines += listing(found, before, after)
    lines += listing(found, "неверно", "верно") + listing(found, "отказ", "верно")
    return lines


def row(label: str, side: dict[str, int]) -> str:
    return f"{label:10}{side['верно']:>8}{side['отказ']:>8}{side['неверно']:>9}{cost(side):>7.3f}"


def listing(found: list[Pair], before: str, after: str) -> list[str]:
    chosen = [p for p in found if p["base"] == before and p["variant"] == after]
    if not chosen:
        return []
    lines = [f"{before} → {after} ({len(chosen)}):"]
    for one in chosen[:SHOWN]:
        lines.append(
            f"  «{one['key']}»: ждали {one['expected']}; "
            f"было {one['base_got']}; стало {one['variant_got']}"
        )
    return lines


def commit() -> str:
    head = git("rev-parse", "--short", "HEAD")
    return head + "+правки" if git("status", "--porcelain") else head


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True).stdout.strip()


def record(folder: Path, entry: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "journal.jsonl").open("a", encoding="utf-8") as journal:
        journal.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_journal(folder: Path) -> list[dict]:
    path = folder / "journal.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def run_dev(stage: str, idea: str) -> None:
    ref, sections = reference(), portal()
    base = BUILDERS[stage](load(stage, "base"), ref, sections, False)
    variant = BUILDERS[stage](load(stage, idea), ref, sections, False)
    found = pairs(base, variant)
    print("\n".join(report(stage, idea, found)))

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = DEV_DIR / "runs" / f"{stamp}-{stage}-{idea}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "items.json").write_text(json.dumps(found, ensure_ascii=False, indent=1))
    if idea != "base":
        source = Path("variants") / stage / f"{idea}.py"
        (folder / "variant.py").write_text(source.read_text(encoding="utf-8"))
    record(
        DEV_DIR,
        {
            "time": stamp,
            "stage": stage,
            "idea": idea,
            "hypothesis": hypothesis(stage, idea),
            "base": counts([p["base"] for p in found]),
            "variant": counts([p["variant"] for p in found]),
            "better": sum(COST[p["variant"]] < COST[p["base"]] for p in found),
            "worse": sum(COST[p["variant"]] > COST[p["base"]] for p in found),
            "commit": commit(),
        },
    )
    print(f"\nВсе примеры: {folder / 'items.json'}")


def run_test(stage: str, idea: str) -> None:
    """Только числа и только при заметном улучшении: примеры test не видит никто."""
    ref, sections = reference(), portal()
    found = BUILDERS[stage](load(stage, idea), ref, sections, True)
    side = counts([one["outcome"] for one in found])
    price = cost(side)
    earlier = [e["cost"] for e in read_journal(TEST_DIR) if e["stage"] == stage]
    best = min(earlier, default=None)
    record(TEST_DIR, {"stage": stage, "idea": idea, "cost": price, "commit": commit()})
    if best is not None and price > best - LADDER_STEP:
        print(f"{stage} {idea}: не лучше прошлого лучшего на test, число не показываем")
        return
    print(f"{stage} {idea} на test: {row('вариант', side)}")


if __name__ == "__main__":
    args = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    if len(args) != 2 or args[0] not in FUNCTIONS:
        sys.exit(__doc__)
    if "--test" in sys.argv:
        run_test(args[0], args[1])
    else:
        run_dev(args[0], args[1])
