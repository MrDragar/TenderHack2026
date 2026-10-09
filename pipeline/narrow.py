from contracts.pipeline import Candidates, FoundChar, Result
from contracts.ref import Position


def narrow(candidates: Candidates, chars: list[FoundChar], ref: list[Position]) -> Result:
    return {
        "category": candidates["category"],
        "steps": [],
        "chars": chars,
        "code": None,
        "question": None,
        "missing_required": [],
        "remarks": [],
    }
