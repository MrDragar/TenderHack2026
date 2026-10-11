"""Ветка semantic: раздел с лучшим сходством к тексту товара и названиям найденных кодов КТРУ.

Перенос rank_portal из ветки codex/semantic-characteristics: запрос это текст без пар
«название: значение» плюс названия кандидатов, сходство как в semantic_fallback, отвечаем
при сходстве от 0.52 и отрыве от второго раздела от 0.03.
"""

from functools import cache

from contracts.ref import Portal, Position
from pipeline.text import without_pairs
from scripts.dataloader import load_data
from variants.candidates.semantic_fallback import find, similarity

THRESHOLD = 0.52
MARGIN = 0.03


@cache
def reference() -> list[Position]:
    return load_data()


def find_section(text: str, portal: Portal) -> str | None:
    names = [c["name"] for c in find(text, reference())]
    query = " ".join([without_pairs(text), *names])
    ranked = []
    for name in portal:
        score = similarity(query, name)
        if score >= THRESHOLD:
            ranked.append((-score, name))
    ranked.sort()
    if not ranked:
        return None
    if len(ranked) > 1 and ranked[1][0] - ranked[0][0] < MARGIN:
        return None
    return ranked[0][1]
