import sys
from pathlib import Path
from random import Random

from openpyxl import load_workbook

from contracts.pipeline import FoundChar, Fragment
from contracts.ref import Portal, Position
from pipeline.candidates import find
from pipeline.extract import NUMBER, extract
from pipeline.link import link
from pipeline.narrow import narrow
from pipeline.section import find_section
from pipeline.values import show
from scripts.generate import Generated, generate, gold_char, inside, section_texts, siblings

RAW_DIR = Path("data/raw/Казань 2026/Примеры выгрузок")
SHEETS = ("Одежда.xlsx", "Мебель.xlsx", "Инструменты.xlsx")
PRODUCTS_PER_SHEET = 200
GENERATED = 300
SECTIONS = 300

type Pairs = list[tuple[str, str]]


def share(part: int, total: int) -> str:
    return f"{part}/{total} ({part / total:.0%})" if total else "0/0"


def stage1(generated: list[Generated], ref: list[Position]) -> str:
    """Соседние коды одного шаблона называются одинаково, их различает сужение, а не этап 1."""
    hits = 0
    sizes = 0
    for example in generated:
        codes = [c["code"] for c in find(example["text"], ref)]
        hits += example["code"] in codes
        sizes += len(codes)
    total = len(generated)
    mean = sizes / total if total else 0
    return (
        f"1. Позиция КТРУ: верный код среди кандидатов {share(hits, total)}, кандидатов {mean:.1f}"
    )


def stage2(texts: list[tuple[str, str]], portal: Portal) -> str:
    right = sum(find_section(text, portal) == section for text, section in texts)
    return f"2. Раздел портала: угадан {share(right, len(texts))}"


def products(names: set[str], raw_dir: Path = RAW_DIR) -> list[tuple[str, Pairs]]:
    """Товары листа 1: название и характеристики через « | ». Между названием характеристики
    и значением разделителя нет, делим по самому длинному названию из листа 2."""
    found = []
    for sheet_name in SHEETS:
        book = load_workbook(raw_dir / sheet_name, read_only=True)
        taken = 0
        for row in book.worksheets[0].iter_rows(min_row=2, values_only=True):
            if taken == PRODUCTS_PER_SHEET:
                break
            pairs = split_pairs(str(row[5] or ""), names)
            if row[1] and pairs:
                found.append((str(row[1]), pairs))
                taken += 1
        book.close()
    return found


def split_pairs(line: str, names: set[str]) -> Pairs:
    pairs = []
    for part in line.split(" | "):
        name = longest_prefix(part, names)
        if name:
            pairs.append((part[: len(name)], part[len(name) :].strip()))
    return pairs


def longest_prefix(part: str, names: set[str]) -> str | None:
    words = part.split()
    for size in range(len(words) - 1, 0, -1):
        prefix = " ".join(words[:size])
        if prefix.casefold() in names:
            return prefix
    return None


def stage3(items: list[tuple[str, Pairs]]) -> str:
    """Пара найдена, если есть кусок с тем же названием и значением; числа сравниваем числом."""
    expected = 0
    hits = 0
    named = 0
    for name, pairs in items:
        text = ", ".join([name] + [f"{n} {v}" for n, v in pairs])
        fragments = [f for f in extract(text) if f["name"]]
        named += len(fragments)
        for pair_name, value in pairs:
            expected += 1
            hits += any(same_pair(f, pair_name, value) for f in fragments)
    return (
        f"3. Извлечение: нашли {share(hits, expected)}, "
        f"из кусков с названием верных {share(hits, named)}"
    )


def same_pair(item: Fragment, name: str, value: str) -> bool:
    if (item["name"] or "").casefold() != name.casefold():
        return False
    number = NUMBER.search(value)
    if item["number"] is not None and number:
        return item["number"] == float(number["number"].replace(",", "."))
    return item["value"].casefold() == value.casefold()


def stage4(generated: list[Generated], by_code: dict[str, Position], ref: list[Position]) -> str:
    expected = 0
    right = 0
    kinds = 0
    right_kinds = 0
    extra = 0
    for example in generated:
        candidates = siblings(by_code[example["code"]], by_code)
        found = link(example["fragments"], candidates, None, ref, {})
        names = {char["name"] for char in example["chars"]}
        extra += sum(char["name"] not in names for char in found)
        for char in example["chars"]:
            expected += 1
            got = [f for f in found if f["name"] == char["name"]]
            right += any(same(f["value"], char["value"]) for f in got)
            if "kind" in char:
                kinds += 1
                right_kinds += any(f["kind"] == char["kind"] for f in got)
    return (
        f"4. Привязка и унификация: название и значение верны {share(right, expected)}, "
        f"вид ошибки {share(right_kinds, kinds)}, лишних {extra}"
    )


def same(a: str | int | float, b: str | int | float) -> bool:
    return show(a).strip().casefold() == show(b).strip().casefold()


def stage5(generated: list[Generated], by_code: dict[str, Position], ref: list[Position]) -> str:
    """Со всеми неизменяемыми значениями код должен найтись; без одного допустим вопрос,
    но не уверенный неверный код."""
    rng = Random(0)
    full_right = 0
    silent = 0
    for example in generated:
        position = by_code[example["code"]]
        candidates = siblings(position, by_code)
        chars = fixed_chars(position)
        full_right += narrow(candidates, chars, ref)["code"] == example["code"]
        if chars:
            chars.pop(rng.randrange(len(chars)))
        code = narrow(candidates, chars, ref)["code"]
        silent += code is not None and code != example["code"]
    total = len(generated)
    return (
        f"5. Сужение: со всеми значениями код верен {share(full_right, total)}, "
        f"без одного молча неверно {silent}"
    )


def fixed_chars(position: Position) -> list[FoundChar]:
    found = []
    for char in position["characteristics"]:
        if char["selection"] != "fixed" or not char["values"]:
            continue
        first = char["values"][0]
        value = first if isinstance(first, str) else inside(first)
        if value is not None:
            found.append(gold_char({"name": char["name"], "value": value}))
    return found


def report(ref: list[Position], portal: Portal, with_products: bool = True) -> str:
    by_code = {p["code"]: p for p in ref}
    generated = generate(ref, GENERATED)
    lines = [
        "Этапы на правильном входе",
        f"  {stage1(generated, ref)}",
        f"  {stage2(section_texts(portal, SECTIONS), portal)}",
    ]
    if with_products and portal and RAW_DIR.exists():
        names = set()
        for chars in portal.values():
            for char in chars:
                names.add(char["name"].casefold())
        lines.append(f"  {stage3(products(names))}")
    lines += [f"  {stage4(generated, by_code, ref)}", f"  {stage5(generated, by_code, ref)}"]
    return "\n".join(lines)


if __name__ == "__main__":
    from pipeline.run import portal, reference

    print(report(reference(), portal(), with_products="--fast" not in sys.argv))
