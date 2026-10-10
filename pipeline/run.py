import json
import sys
from functools import cache

from contracts.pipeline import (
    Candidate,
    Extract,
    FindCandidates,
    FindSection,
    Link,
    Narrow,
    Remark,
    Result,
)
from contracts.ref import Portal, Position
from pipeline.candidates import find
from pipeline.explain import NAME_WHY, remarks
from pipeline.extract import extract
from pipeline.link import link
from pipeline.narrow import narrow
from pipeline.section import find_section
from pipeline.text import correct, stems, unknown, vocabulary, words
from scripts.dataloader import DEFAULT_PORTAL_PATH, load_data, load_portal


@cache
def reference() -> list[Position]:
    return load_data()


@cache
def portal() -> Portal:
    # Без собранного листа 2 работаем на одном КТРУ, а не падаем.
    if not DEFAULT_PORTAL_PATH.exists():
        return {}
    return load_portal()


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
) -> Result:
    """Этапы подменяются вариантом или эталоном, так замер сравнивает их по отдельности."""
    found = find(text, ref)
    section = find_section(text, sections)
    chars = link(extract(text), found, section, ref, sections)
    narrowed = narrow(found, chars, ref)
    taken = [(char["start"], char["end"]) for char in chars]
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
        "unknown": unknown(text, taken, known(found, ref)),
    }


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
