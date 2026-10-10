from contracts.pipeline import Candidate
from contracts.ref import Position
from pipeline.text import best_named


def find(text: str, ref: list[Position]) -> list[Candidate]:
    """Конечные коды с лучшим совпадением названия, пусто, если совпало меньше половины."""
    finals = [p for p in ref if p["position_type"] == "final"]
    top = best_named(text, finals, [p["name"] for p in finals])
    return [{"code": p["code"], "name": p["name"], "score": 1 / len(top)} for p in top]
