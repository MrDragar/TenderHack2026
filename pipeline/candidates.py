from contracts.pipeline import Candidates
from contracts.ref import Position


def find(text: str, ref: list[Position]) -> Candidates:
    return {"category": None, "candidates": []}
