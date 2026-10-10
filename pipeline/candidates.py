from contracts.pipeline import Candidates
from contracts.ref import Position
from pipeline.text import correct, stems, vocabulary, without_pairs, words

MIN_SHARE = 0.5


def find(text: str, ref: list[Position]) -> Candidates:
    """Конечные коды с лучшим совпадением названия, пусто, если совпало меньше половины."""
    finals = [p for p in ref if p["position_type"] == "final"]
    vocab = vocabulary(p["name"] for p in finals)
    text_stems = {word["stem"] for word in correct(words(without_pairs(text)), vocab)}
    if not text_stems:
        return {"category": None, "candidates": []}

    ranked = []
    for position in finals:
        name_stems = stems(position["name"])
        if name_stems:
            ranked.append((rank(name_stems, text_stems), position))
    if not ranked:
        return {"category": None, "candidates": []}

    best = max(position_rank for position_rank, _ in ranked)
    if best[0] < MIN_SHARE:
        return {"category": None, "candidates": []}
    top = [position for position_rank, position in ranked if position_rank == best]
    score = 1 / len(top)
    return {
        "category": top[0]["category"],
        "candidates": [{"code": p["code"], "name": p["name"], "score": score} for p in top],
    }


def rank(name_stems: set[str], text_stems: set[str]) -> tuple[float, int, int]:
    """Больше лучше: доля совпавших слов, затем их число, затем меньше лишних в названии."""
    hits = len(name_stems & text_stems)
    # Доля от меньшего из двух, чтобы короткий запрос «Перчатки» находил длинные названия.
    share = hits / min(len(name_stems), len(text_stems))
    extra = len(name_stems) - hits
    return share, hits, -extra
