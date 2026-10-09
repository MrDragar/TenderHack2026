from contracts.pipeline import Candidates
from contracts.ref import Position
from pipeline.text import stems

MIN_SHARE = 0.5


def find(text: str, ref: list[Position]) -> Candidates:
    """Конечные коды с лучшим совпадением названия, пусто, если совпало меньше половины."""
    text_stems = stems(text)
    if not text_stems:
        return {"category": None, "candidates": []}

    ranked = []
    for position in ref:
        name_stems = stems(position["name"])
        if position["position_type"] == "final" and name_stems:
            ranked.append((rank(name_stems, text_stems), position))
    if not ranked:
        return {"category": None, "candidates": []}

    best = max(position_rank for position_rank, _ in ranked)
    share = best[0]
    if share < MIN_SHARE:
        return {"category": None, "candidates": []}
    top = [position for position_rank, position in ranked if position_rank == best]
    return {
        "category": top[0]["category"],
        "candidates": [{"code": p["code"], "name": p["name"], "score": share} for p in top],
    }


def rank(name_stems: set[str], text_stems: set[str]) -> tuple[float, int, int]:
    """Больше лучше: доля совпавших слов, затем их число, затем меньше лишних в названии."""
    hits = len(name_stems & text_stems)
    # Доля от меньшего из двух, чтобы короткий запрос «Перчатки» находил длинные названия.
    share = hits / min(len(name_stems), len(text_stems))
    extra = len(name_stems) - hits
    return share, hits, -extra
