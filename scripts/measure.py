import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypedDict

from contracts.eval import ExpectedChar, GoldExample
from contracts.pipeline import (
    Candidate,
    Candidates,
    ExtractChars,
    FindCandidates,
    FoundChar,
    Question,
    Result,
    Run,
)
from contracts.ref import Portal, Position
from pipeline.chars import extract
from pipeline.narrow import show
from pipeline.run import run

DEFAULT_GOLD_PATH = Path("data/eval/gold.json")
MAX_QUESTIONS = 3
RECALL_AT = (1, 5, 20)

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
    rank: int | None
    replies: list[str]
    extra_questions: int
    repeated: bool
    chars: Chars


def same(a: str | int | float, b: str | int | float) -> bool:
    # В справочнике размер то строка «48», то число 48, сравниваем как текст.
    return show(a).strip().lower() == show(b).strip().lower()


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
            values = ", ".join(show(v) for v in char["values"])
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
        "rank": rank(expected["code"], find_codes(example["text"])),
        "replies": replies,
        "extra_questions": extra_questions,
        "repeated": repeated,
        "chars": check_chars(expected["chars"], first["chars"]),
    }


def rank(code: str | None, codes: list[str]) -> int | None:
    if code not in codes:
        return None
    return codes.index(code) + 1


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
    in_candidates = sum(d["rank"] is not None for d in known)
    not_found = sum(is_right(d) for d in unknown)
    times = [d["seconds"] for d in dialogs]
    mean = sum(times) / len(times)
    lines = [
        f"Категория: {share(categories, len(dialogs))}",
        f"Кандидаты: правильный код среди кандидатов {share(in_candidates, len(known))}",
        f"Характеристики: {chars_line(dialogs)}",
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
    return f"нашли {share(ok, expected)}, из найденных верных {share(ok, found)}"


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
    if expected["code"] and dialog["rank"] is None:
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


def gold_find(example: GoldExample, by_code: dict[str, Position]) -> FindCandidates:
    found = gold_candidates(example, by_code)

    def find(text: str, ref: list[Position]) -> Candidates:
        return found

    return find


def gold_candidates(example: GoldExample, by_code: dict[str, Position]) -> Candidates:
    """Все конечные коды шаблона эталонного кода: сужению остаётся различить соседей."""
    position = by_code.get(example["expected"]["code"] or "")
    if position is None:
        return {"category": None, "candidates": []}
    return {"category": position["category"], "candidates": siblings(position, by_code)}


def siblings(position: Position, by_code: dict[str, Position]) -> list[Candidate]:
    parent = position["parent_code"]
    if parent is None:
        family = [position]
    else:
        family = []
        for p in by_code.values():
            if p["parent_code"] == parent and p["position_type"] == "final":
                family.append(p)
    score = 1 / len(family)
    return [{"code": p["code"], "name": p["name"], "score": score} for p in family]


def gold_extract(example: GoldExample) -> ExtractChars:
    """Эталонные характеристики, а ответы на вопросы, дописанные к тексту, разбирает этап."""
    told = [gold_char(char) for char in example["expected"]["chars"]]

    def extract_told(
        text: str, candidates: Candidates, ref: list[Position], portal: Portal
    ) -> list[FoundChar]:
        replies = text[len(example["text"]) :]
        return told + extract(replies, candidates, ref, portal)

    return extract_told


def gold_char(char: ExpectedChar) -> FoundChar:
    value = char["value"]
    return {
        "name": char["name"],
        "value": value,
        "unit": None,
        "source": "ktru",
        "fragment": "",
        "fix": None,
    }


def ladder(
    gold: list[GoldExample], find_codes: FindCodes, by_code: dict[str, Position]
) -> dict[str, list[Dialog]]:
    """Сквозной прогон, где этапы по очереди заменены эталоном: видно, где теряется код."""
    rows: dict[str, list[Dialog]] = {
        "как есть": [],
        "эталонные кандидаты": [],
        "и характеристики": [],
    }
    for example in gold:
        find = gold_find(example, by_code)
        told = gold_extract(example)
        rows["как есть"].append(talk(example, run, find_codes, by_code))
        with_find = talk(example, staged(find), find_codes, by_code)
        rows["эталонные кандидаты"].append(with_find)
        with_chars = talk(example, staged(find, told), find_codes, by_code)
        rows["и характеристики"].append(with_chars)
    return rows


def staged(find: FindCandidates, extract: ExtractChars = extract) -> Run:
    return lambda text: run(text, find=find, extract=extract)


def stages_report(rows: dict[str, list[Dialog]]) -> str:
    """Каждый этап мерится на эталонном входе, поэтому ошибки соседей его цифры не портят."""
    known = [d for d in rows["как есть"] if d["example"]["expected"]["code"]]
    lines = [
        "Этапы на эталонном входе",
        f"  Кандидаты: {recall_line(known)}",
        f"  Характеристики на эталонных кандидатах: {chars_line(rows['эталонные кандидаты'])}",
        f"  Сужение на эталонных характеристиках: {code_line(rows['и характеристики'])}",
        "Лесенка, сквозная точность",
    ]
    for name, dialogs in rows.items():
        lines.append(f"  {name}: {code_line(dialogs)}")
    return "\n".join(lines)


def recall_line(known: list[Dialog]) -> str:
    parts = []
    for k in RECALL_AT:
        hits = sum(d["rank"] is not None and d["rank"] <= k for d in known)
        parts.append(f"Recall@{k} {share(hits, len(known))}")
    reciprocal = [1 / d["rank"] for d in known if d["rank"] is not None]
    mrr = sum(reciprocal) / len(known) if known else 0
    parts.append(f"MRR {mrr:.2f}")
    return ", ".join(parts)


def code_line(dialogs: list[Dialog]) -> str:
    right = sum(is_right(d) for d in dialogs)
    silent = sum(silently_wrong(d) for d in dialogs)
    asked = sum(len(d["replies"]) for d in dialogs)
    mean = asked / len(dialogs) if dialogs else 0
    return f"верно {share(right, len(dialogs))}, молча неверно {silent}, вопросов {mean:.1f}"


def silently_wrong(dialog: Dialog) -> bool:
    """Уверенно выдали код, и он неверный: хуже вопроса и хуже «не нашли»."""
    code = dialog["final"]["code"]
    return code is not None and code != dialog["example"]["expected"]["code"]


def load_gold(path: Path = DEFAULT_GOLD_PATH) -> list[GoldExample]:
    with path.open(encoding="utf-8") as source:
        return json.load(source)


if __name__ == "__main__":
    from pipeline.candidates import find
    from pipeline.run import reference

    ref = reference()
    by_code = {position["code"]: position for position in ref}

    def find_codes(text: str) -> list[str]:
        return [c["code"] for c in find(text, ref)["candidates"]]

    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_GOLD_PATH
    rows = ladder(load_gold(path), find_codes, by_code)
    print(stages_report(rows))
    print()
    print(report(rows["как есть"]))
