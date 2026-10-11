"""Ветка semantic: при отказе поиска берём позиции, похожие по словам с синонимами и по написанию.

Перенос retrieve из ветки codex/semantic-characteristics: при пустом ответе бейзлайна
сходство названия (покрытие основ после словаря понятий или похожесть строк) с весом 0.85
и слова характеристик позиции с весом 0.15, порог 0.45, все в пределах 0.08 от лучшего.
"""

import re
import unicodedata
from difflib import SequenceMatcher

from contracts.pipeline import Candidate
from contracts.ref import Position
from pipeline.candidates import find as lexical_find
from pipeline.text import stems, without_pairs

MAX_CANDIDATES = 20
THRESHOLD = 0.45
SPREAD = 0.08
CONCEPTS = {
    "верстак": "стол",
    "офисный": "письменный",
    "рабочий": "письменный",
    "ростовка": "рост",
    "габарит": "размер",
    "масса": "вес",
    "окраска": "цвет",
    "оттенок": "цвет",
    "сырье": "материал",
    "сырья": "материал",
}
DESCRIBED: dict[str, set[str]] = {}


def find(text: str, ref: list[Position]) -> list[Candidate]:
    return lexical_find(text, ref) or rank(without_pairs(text), ref)


def rank(text: str, ref: list[Position]) -> list[Candidate]:
    terms = semantic_terms(text)
    if not terms:
        return []
    ranked = []
    for position in ref:
        if position["position_type"] != "final":
            continue
        score = 0.85 * similarity(text, position["name"]) + 0.15 * evidence(terms, position)
        if score >= THRESHOLD:
            ranked.append((score, position))
    if not ranked:
        return []
    ranked.sort(key=lambda item: (-item[0], item[1]["code"]))
    kept = [item for item in ranked if item[0] >= ranked[0][0] - SPREAD][:MAX_CANDIDATES]
    total = sum(score for score, _ in kept)
    return [{"code": p["code"], "name": p["name"], "score": s / total} for s, p in kept]


def similarity(left: str, right: str) -> float:
    left_terms, right_terms = semantic_terms(left), semantic_terms(right)
    if not left_terms or not right_terms:
        return 0.0
    hits = len(left_terms & right_terms)
    coverage = hits / len(right_terms)
    containment = hits / min(len(left_terms), len(right_terms))
    sequence = SequenceMatcher(None, normalized(left), normalized(right)).ratio()
    return max(coverage, containment * 0.9, sequence * 0.65)


def evidence(terms: set[str], position: Position) -> float:
    if position["code"] not in DESCRIBED:
        DESCRIBED[position["code"]] = described(position)
    return min(1.0, len(terms & DESCRIBED[position["code"]]) / 3)


def described(position: Position) -> set[str]:
    concepts = []
    for char in position["characteristics"]:
        concepts.append(char["name"])
        concepts.extend(str(value) for value in char["values"] if isinstance(value, str))
    return semantic_terms(" ".join(concepts))


def semantic_terms(text: str) -> set[str]:
    return stems(" ".join(CONCEPTS.get(word, word) for word in normalized(text).split()))


def normalized(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    return " ".join(re.sub(r"[^\w]+", " ", folded).replace("_", " ").split())
