from contracts.pipeline import Candidates
from contracts.ref import Position
from pipeline.text import stems

MIN_SCORE = 0.5


def find(text: str, ref: list[Position]) -> Candidates:
    text_stems = stems(text)
    if not text_stems:
        return {"category": None, "candidates": []}
    scored = []
    for position in ref:
        name = stems(position["name"])
        if position["position_type"] != "final" or not name:
            continue
        hits = len(name & text_stems)
        score = hits / min(len(name), len(text_stems))
        scored.append(((score, hits, -len(name)), position))
    if not scored:
        return {"category": None, "candidates": []}

    # Короткий запрос «Перчатки» должен находить длинные названия, поэтому доля считается от
    # меньшего из двух. При равной доле выигрывает больше совпавших слов, потом меньше лишних.
    best = max(key for key, _ in scored)
    if best[0] < MIN_SCORE:
        return {"category": None, "candidates": []}
    top = [position for key, position in scored if key == best]
    return {
        "category": top[0]["category"],
        "candidates": [{"code": p["code"], "name": p["name"], "score": best[0]} for p in top],
    }
