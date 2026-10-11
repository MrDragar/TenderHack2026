import json
import sys

from contracts.pipeline import (
    Candidate,
    Extract,
    FindCandidates,
    FindSection,
    FoundChar,
    Judge,
    Leftover,
    Link,
    Narrow,
    Remark,
    Result,
)
from contracts.ref import Portal, Position
from pipeline.candidates import find
from pipeline.data import portal, reference
from pipeline.explain import NAME_WHY, remarks
from pipeline.extract import extract
from pipeline.link import link
from pipeline.narrow import narrow
from pipeline.section import find_section
from pipeline.text import correct, stems, unknown, vocabulary, words
from pipeline.trash import judge


def run(text: str) -> Result:
    return solve(text, reference(), portal())


def solve(
    text: str,
    ref: list[Position],
    sections: Portal,
    find: FindCandidates = find,
    find_section: FindSection = find_section,
    extract: Extract = extract,
    link: Link = link,
    narrow: Narrow = narrow,
    judge: Judge = judge,
) -> Result:
    """Этапы подменяются вариантом или эталоном, так замер сравнивает их по отдельности."""
    found = find(text, ref)
    section = find_section(text, sections)
    linked = link(extract(text), found, section, ref, sections)
    taken = [(char["start"], char["end"]) for char in linked]
    chars, left = checked(
        linked, unknown(text, taken, known(found, ref)), product(found, section), judge
    )
    narrowed = narrow(found, chars, ref)
    return {
        "category": narrowed["category"],
        "section": section,
        "steps": narrowed["steps"],
        "chars": chars,
        "code": narrowed["code"],
        "question": narrowed["question"],
        "missing_required": narrowed["missing_required"],
        "remarks": name_fixes(text, found, taken) + remarks(chars),
        "candidates": narrowed["candidates"],
        "options": narrowed["options"],
        "unknown": left,
    }


def checked(
    chars: list[FoundChar], rest: list[str], product: str, judge: Judge
) -> tuple[list[FoundChar], list[Leftover]]:
    """Новые характеристики и остаток текста проверяем на мусор одним вызовом модели. Мусор
    не создаём: он уходит в остаток с объяснением, что поправить."""
    new = [char for char in chars if char["source"] == "new"]
    checks = judge([char["fragment"] for char in new] + rest, product)
    by_char = {id(char): check for char, check in zip(new, checks[: len(new)], strict=True)}
    kept: list[FoundChar] = []
    left: list[Leftover] = []
    for char in chars:
        check = by_char.get(id(char))
        if check is None:
            kept.append(char)
        elif check["verdict"] == "trash":
            left.append({"text": char["fragment"], "check": check})
        else:
            char["check"] = check
            char["name"] = check["name"] or char["name"]
            kept.append(char)
    for word, check in zip(rest, checks[len(new) :], strict=True):
        left.append({"text": word, "check": check})
    return kept, left


def product(found: list[Candidate], section: str | None) -> str:
    """Посылка для модели: без вида товара «размер 42» не отличить от мусора."""
    if found:
        return found[0]["name"]
    return section or "Товар"


def name_fixes(text: str, found: list[Candidate], taken: list[tuple[int, int]]) -> list[Remark]:
    """Исправленные слова названия товара: «cnjk» → «стол», иначе правка не видна."""
    vocab = vocabulary(c["name"] for c in found)
    by_stem = {stem: word for word, stem in vocab.items()}
    fixes: list[Remark] = []
    for word in correct(words(text), vocab):
        inside = any(start <= word["start"] < end for start, end in taken)
        if word["fix"] is None or inside or word["stem"] not in by_stem:
            continue
        was = text[word["start"] : word["end"]]
        now = by_stem[word["stem"]]
        fixes.append({"was": was, "now": now, "why": NAME_WHY[word["fix"]], "kind": word["fix"]})
    return fixes


def known(found: list[Candidate], ref: list[Position]) -> set[str]:
    """Слова названий кандидатов и их характеристик уже поняты, даже если значения нет."""
    codes = {c["code"] for c in found}
    names = []
    for position in ref:
        if position["code"] in codes:
            names.append(position["name"])
            names.extend(char["name"] for char in position["characteristics"])
    return stems(" ".join(names))


if __name__ == "__main__":
    print(json.dumps(run(" ".join(sys.argv[1:])), ensure_ascii=False, indent=2))
