from collections.abc import Callable
from typing import Literal, TypedDict

from contracts.ref import Category, Portal, Position, Unit


class Candidate(TypedDict):
    code: str
    name: str
    score: float


class Candidates(TypedDict):
    category: Category | None
    candidates: list[Candidate]


class FoundChar(TypedDict):
    name: str
    value: str | int | float
    unit: Unit | None
    source: Literal["ktru", "portal", "new"]
    fragment: str


class Step(TypedDict):
    what: str
    left: int


class Question(TypedDict):
    char: str
    options: list[str]


class Remark(TypedDict):
    was: str
    now: str
    why: str


class Result(TypedDict):
    category: Category | None
    steps: list[Step]
    chars: list[FoundChar]
    code: str | None
    question: Question | None
    missing_required: list[str]
    remarks: list[Remark]


type FindCandidates = Callable[[str, list[Position]], Candidates]
type ExtractChars = Callable[[str, Candidates, list[Position], Portal], list[FoundChar]]
type Narrow = Callable[[Candidates, list[FoundChar], list[Position]], Result]
type Run = Callable[[str], Result]
