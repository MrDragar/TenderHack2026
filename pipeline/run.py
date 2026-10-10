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


def run(
    text: str,
    find: FindCandidates = find,
    find_section: FindSection = find_section,
    extract: Extract = extract,
    link: Link = link,
    narrow: Narrow = narrow,
) -> Result:
    """Этапы подменяются вариантом или эталоном, так замер сравнивает их по отдельности."""
    ref = reference()
    found = find(text, ref)
    section = find_section(text, portal())
    result = narrow(found, link(extract(text), found, section, ref, portal()), ref)
    result["section"] = section
    result["unknown"] = unknown(text, covered(result), known(found, ref))
    result["remarks"] = name_fixes(text, found, covered(result)) + result["remarks"]
    return result


def name_fixes(text: str, found: list[Candidate], taken: list[tuple[int, int]]) -> list[Remark]:
    """Исправленные слова названия товара: «cnjk» → «стол», иначе правка не видна."""
    vocab = vocabulary(c["name"] for c in found)
    by_stem = {stem: word for word, stem in vocab.items()}
    fixes: list[Remark] = []
    for word in correct(words(text), vocab):
        inside = any(start <= word["start"] < end for start, end in taken)
        if word["fix"] is None or inside or word["stem"] not in by_stem:
            continue
        why = "опечатка в названии товара"
        if word["fix"] == "layout":
            why = "набрано в английской раскладке или латиницей"
        was = text[word["start"] : word["end"]]
        fixes.append({"was": was, "now": by_stem[word["stem"]], "why": why, "kind": word["fix"]})
    return fixes


def covered(result: Result) -> list[tuple[int, int]]:
    return [(char["start"], char["end"]) for char in result["chars"]]


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
