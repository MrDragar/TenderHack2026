import json
import sys
import time
from pathlib import Path
from typing import TypedDict

from contracts.eval import ExpectedChar, GoldExample
from contracts.pipeline import FoundChar, Result, Run

DEFAULT_GOLD_PATH = Path("data/eval/gold.json")


class Checked(TypedDict):
    example: GoldExample
    result: Result
    seconds: float
    category_ok: bool
    code_ok: bool
    chars_ok: int
    chars_wrong: list[str]
    chars_missed: list[str]


def same(a: str | int | float, b: str | int | float) -> bool:
    # В справочнике размер то строка «48», то число 48, сравниваем как текст.
    return show(a).lower() == show(b).lower()


def show(value: str | int | float) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def check_chars(
    expected: list[ExpectedChar], found: list[FoundChar]
) -> tuple[int, list[str], list[str]]:
    ok, wrong, missed = 0, [], []
    for char in expected:
        values = [f["value"] for f in found if f["name"] == char["name"]]
        if any(same(v, char["value"]) for v in values):
            ok += 1
        elif values:
            got = ", ".join(show(v) for v in values)
            wrong.append(f"{char['name']}: ждали {show(char['value'])}, получили {got}")
        else:
            missed.append(f"{char['name']}: {show(char['value'])}")
    return ok, wrong, missed


def check(example: GoldExample, run: Run) -> Checked:
    start = time.perf_counter()
    result = run(example["text"])
    seconds = time.perf_counter() - start
    expected = example["expected"]
    ok, wrong, missed = check_chars(expected["chars"], result["chars"])
    return {
        "example": example,
        "result": result,
        "seconds": seconds,
        "category_ok": result["category"] == expected["category"],
        "code_ok": result["code"] == expected["code"],
        "chars_ok": ok,
        "chars_wrong": wrong,
        "chars_missed": missed,
    }


def share(part: int, total: int) -> str:
    return f"{part}/{total} ({part / total:.0%})" if total else "0/0"


def report(checked: list[Checked]) -> str:
    total = len(checked)
    categories = sum(c["category_ok"] for c in checked)
    codes = sum(c["code_ok"] for c in checked)
    chars = sum(c["chars_ok"] for c in checked)
    chars_total = sum(len(c["example"]["expected"]["chars"]) for c in checked)
    times = [c["seconds"] for c in checked]
    mean = sum(times) / total
    lines = [
        f"Категория: {share(categories, total)}",
        f"Код: {share(codes, total)}",
        f"Характеристики: {share(chars, chars_total)}",
        f"Время: среднее {mean:.2f} с, максимум {max(times):.2f} с",
    ]
    for c in checked:
        if not is_clean(c):
            lines += ["", *errors(c)]
    return "\n".join(lines)


def is_clean(c: Checked) -> bool:
    return c["code_ok"] and c["category_ok"] and not c["chars_wrong"] and not c["chars_missed"]


def errors(c: Checked) -> list[str]:
    expected = c["example"]["expected"]
    result = c["result"]
    lines = [f"«{c['example']['text']}»"]
    if not c["category_ok"]:
        lines.append(f"  категория: ждали {expected['category']}, получили {result['category']}")
    if not c["code_ok"]:
        lines.append(f"  код: ждали {expected['code']}, получили {result['code']}")
    if result["question"]:
        lines.append(f"  спросили про {result['question']['char']}")
    lines += [f"  неверно {w}" for w in c["chars_wrong"]]
    lines += [f"  не нашли {m}" for m in c["chars_missed"]]
    return lines


def load_gold(path: Path = DEFAULT_GOLD_PATH) -> list[GoldExample]:
    with path.open(encoding="utf-8") as source:
        return json.load(source)


if __name__ == "__main__":
    from pipeline.run import reference, run

    reference()
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_GOLD_PATH
    print(report([check(example, run) for example in load_gold(path)]))
