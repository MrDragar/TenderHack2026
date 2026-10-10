import json
import sys
from functools import cache

from contracts.pipeline import Candidates, ExtractChars, FindCandidates, Narrow, Remark, Result
from contracts.ref import Position
from pipeline.candidates import find
from pipeline.chars import extract
from pipeline.narrow import narrow
from pipeline.text import correct, stems, unknown, vocabulary, words
from scripts.dataloader import load_data


@cache
def reference() -> list[Position]:
    return load_data()


def run(
    text: str,
    find: FindCandidates = find,
    extract: ExtractChars = extract,
    narrow: Narrow = narrow,
) -> Result:
    """Этапы подменяются вариантом или эталоном, так замер сравнивает их по отдельности."""
    ref = reference()
    found = find(text, ref)
    result = narrow(found, extract(text, found, ref, {}), ref)
    result["unknown"] = unknown(text, covered(result), known(found, ref))
    result["remarks"] = name_fixes(text, found, covered(result)) + result["remarks"]
    return result


def name_fixes(text: str, found: Candidates, taken: list[tuple[int, int]]) -> list[Remark]:
    """Исправленные слова названия товара: «cnjk» → «стол», иначе правка не видна."""
    vocab = vocabulary(c["name"] for c in found["candidates"])
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


def known(found: Candidates, ref: list[Position]) -> set[str]:
    """Слова названий кандидатов и их характеристик уже поняты, даже если значения нет."""
    codes = {c["code"] for c in found["candidates"]}
    names = []
    for position in ref:
        if position["code"] in codes:
            names.append(position["name"])
            names.extend(char["name"] for char in position["characteristics"])
    return stems(" ".join(names))


if __name__ == "__main__":
    from pipeline.semantic import run as semantic_run

    print(json.dumps(semantic_run(" ".join(sys.argv[1:])), ensure_ascii=False, indent=2))
