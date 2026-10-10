import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypedDict

from contracts.eval import ExpectedChar, GoldExample
from contracts.pipeline import FoundChar, Question, Result, Run
from contracts.ref import Position
from pipeline.narrow import show as show_value

DEFAULT_GOLD_PATH = Path("data/eval/gold.json")
MAX_QUESTIONS = 3

type FindCodes = Callable[[str], list[str]]


class Chars(TypedDict):
    ok: int
    found: int
    wrong: list[str]
    missed: list[str]


class Dialog(TypedDict):
    example: GoldExample
    first: Result
    final: Result
    seconds: float
    in_candidates: bool
    replies: list[str]
    extra_questions: int
    repeated: bool
    chars: Chars


def same(a: str | int | float, b: str | int | float) -> bool:
    # В справочнике размер то строка «48», то число 48, сравниваем как текст.
    return show(a).lower() == show(b).lower()


def show(value: str | int | float) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def check_chars(expected: list[ExpectedChar], found: list[FoundChar]) -> Chars:
    """Эталон перечисляет все характеристики из текста, поэтому найденное сверх него ошибка."""
    expected_names = {char["name"] for char in expected}
    chars: Chars = {"ok": 0, "found": len(found), "wrong": [], "missed": []}
    for char in expected:
        values = [f["value"] for f in found if f["name"] == char["name"]]
        if any(same(v, char["value"]) for v in values):
            chars["ok"] += 1
        elif values:
            got = ", ".join(show(v) for v in values)
            chars["wrong"].append(f"{char['name']}: ждали {show(char['value'])}, получили {got}")
        else:
            chars["missed"].append(f"{char['name']}: {show(char['value'])}")
    for f in found:
        if f["name"] not in expected_names:
            chars["wrong"].append(f"лишнее {f['name']}: {show(f['value'])} из «{f['fragment']}»")
    return chars


def reply(question: Question, position: Position | None) -> str | None:
    """Пользователь отвечает значением загаданного кода и только на заданный вопрос."""
    if position is None:
        return None
    for char in position["characteristics"]:
        if char["name"] == question["char"] and char["selection"] == "fixed":
            values = ", ".join(show_value(v) for v in char["values"])
            return f"{char['name']}: {values}"
    return None


def talk(
    example: GoldExample, run: Run, find_codes: FindCodes, by_code: dict[str, Position]
) -> Dialog:
    """Прогон с ответами на вопросы пайплайна, пока он не выберет код или не спросит лишнего."""
    expected = example["expected"]
    position = by_code.get(expected["code"] or "")
    text = example["text"]
    start = time.perf_counter()
    first = run(text)
    seconds = time.perf_counter() - start

    result = first
    asked: list[str] = []
    replies: list[str] = []
    extra_questions = 0
    repeated = False
    while result["question"] and len(replies) < MAX_QUESTIONS:
        question = result["question"]
        if question["char"] in asked:
            repeated = True
            break
        answer = reply(question, position)
        if answer is None:
            break
        asked.append(question["char"])
        if was_in_text(question, expected["chars"]):
            extra_questions += 1
            replies.append(f"{answer}», хотя это было в тексте")
        else:
            replies.append(f"{answer}»")
        text = f"{text}. {answer}"
        result = run(text)

    return {
        "example": example,
        "first": first,
        "final": result,
        "seconds": seconds,
        "in_candidates": expected["code"] in find_codes(example["text"]),
        "replies": replies,
        "extra_questions": extra_questions,
        "repeated": repeated,
        "chars": check_chars(expected["chars"], first["chars"]),
    }


def was_in_text(question: Question, expected: list[ExpectedChar]) -> bool:
    return any(char["name"] == question["char"] for char in expected)


def share(part: int, total: int) -> str:
    return f"{part}/{total} ({part / total:.0%})" if total else "0/0"


def is_right(dialog: Dialog) -> bool:
    expected = dialog["example"]["expected"]
    if expected["code"] is None:
        return dialog["first"]["code"] is None and dialog["first"]["question"] is None
    return dialog["final"]["code"] == expected["code"]


def report(dialogs: list[Dialog]) -> str:
    known = [d for d in dialogs if d["example"]["expected"]["code"]]
    unknown = [d for d in dialogs if not d["example"]["expected"]["code"]]
    categories = sum(
        d["first"]["category"] == d["example"]["expected"]["category"] for d in dialogs
    )
    in_candidates = sum(d["in_candidates"] for d in known)
    not_found = sum(is_right(d) for d in unknown)
    times = [d["seconds"] for d in dialogs]
    mean = sum(times) / len(times)
    lines = [
        f"Категория: {share(categories, len(dialogs))}",
        f"Кандидаты: правильный код среди кандидатов {share(in_candidates, len(known))}",
        chars_line(dialogs),
        f"Код: {share(sum(is_right(d) for d in known), len(known))}",
        *code_breakdown(known),
        f"Нет в КТРУ, ответили «не нашли»: {share(not_found, len(unknown))}",
        f"Время: среднее {mean:.2f} с, максимум {max(times):.2f} с",
    ]
    for dialog in dialogs:
        if not is_clean(dialog):
            lines += ["", *errors(dialog)]
    return "\n".join(lines)


def chars_line(dialogs: list[Dialog]) -> str:
    ok = sum(d["chars"]["ok"] for d in dialogs)
    expected = sum(len(d["example"]["expected"]["chars"]) for d in dialogs)
    found = sum(d["chars"]["found"] for d in dialogs)
    return f"Характеристики: нашли {share(ok, expected)}, из найденных верных {share(ok, found)}"


def code_breakdown(known: list[Dialog]) -> list[str]:
    by_questions: dict[int, int] = {}
    wrong = 0
    stuck = 0
    for dialog in known:
        if is_right(dialog):
            asked = len(dialog["replies"])
            by_questions[asked] = by_questions.get(asked, 0) + 1
        elif dialog["final"]["code"]:
            wrong += 1
        else:
            stuck += 1
    lines = []
    for asked in sorted(by_questions):
        lines.append(f"  {questions_label(asked)}: {by_questions[asked]}")
    extra = sum(d["extra_questions"] for d in known)
    lines += [
        f"  выбрали неверный код: {wrong}",
        f"  не дошли до кода: {stuck}",
        f"  лишних вопросов, ответ был в тексте: {extra}",
    ]
    return lines


def questions_label(asked: int) -> str:
    if asked == 0:
        return "сразу"
    if asked == 1:
        return "после 1 вопроса"
    return f"после {asked} вопросов"


def is_clean(dialog: Dialog) -> bool:
    chars = dialog["chars"]
    return (
        is_right(dialog)
        and dialog["extra_questions"] == 0
        and not chars["wrong"]
        and not chars["missed"]
    )


def errors(dialog: Dialog) -> list[str]:
    example = dialog["example"]
    expected = example["expected"]
    final = dialog["final"]
    lines = [f"«{example['text']}»"]
    if dialog["first"]["category"] != expected["category"]:
        lines.append(
            f"  категория: ждали {expected['category']}, получили {dialog['first']['category']}"
        )
    if expected["code"] and not dialog["in_candidates"]:
        lines.append("  правильного кода нет среди кандидатов")
    lines += [f"  спросили, ответили «{answer}" for answer in dialog["replies"]]
    if dialog["repeated"]:
        lines.append("  пайплайн не принял ответ и повторил вопрос")
    if final["question"] and not is_right(dialog):
        lines.append(f"  остался без ответа вопрос про {final['question']['char']}")
    if not is_right(dialog):
        wanted = outcome(expected["code"], None)
        got = outcome(final["code"], final["question"])
        lines.append(f"  ответ: ждали {wanted}, получили {got}")
    lines += [f"  неверно {w}" for w in dialog["chars"]["wrong"]]
    lines += [f"  не нашли {m}" for m in dialog["chars"]["missed"]]
    return lines


def outcome(code: str | None, question: Question | None) -> str:
    if code:
        return f"код {code}"
    if question:
        return "вопрос"
    return "«не нашли»"


def load_gold(path: Path = DEFAULT_GOLD_PATH) -> list[GoldExample]:
    with path.open(encoding="utf-8") as source:
        return json.load(source)


if __name__ == "__main__":
    from pipeline.run import find_candidates, reference, run

    ref = reference()
    by_code = {position["code"]: position for position in ref}

    def find_codes(text: str) -> list[str]:
        return [c["code"] for c in find_candidates(text, ref)["candidates"]]

    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_GOLD_PATH
    dialogs = [talk(example, run, find_codes, by_code) for example in load_gold(path)]
    print(report(dialogs))
