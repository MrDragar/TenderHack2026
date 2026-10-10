from collections.abc import Callable
from typing import Literal, TypedDict

from contracts.ref import Category, Portal, Position, Unit


class Candidate(TypedDict):
    code: str
    name: str
    score: float


type FixKind = Literal[
    "typo", "layout", "unit", "wrong_unit", "out_of_range", "not_in_list", "conflict"
]


class Fragment(TypedDict):
    form: Literal["pair", "number", "words"]
    name: str | None
    value: str
    text: str
    number: float | None
    unit: str | None
    start: int
    end: int


class FoundChar(TypedDict):
    name: str
    value: str | int | float
    unit: Unit | None
    source: Literal["ktru", "portal", "new"]
    fragment: str
    start: int
    end: int
    required: bool
    fix: str | None
    kind: FixKind | None


class Step(TypedDict):
    what: str
    left: int
    ignored: bool


class Question(TypedDict):
    char: str
    options: list[str]


class Remark(TypedDict):
    was: str
    now: str
    why: str
    kind: FixKind


class Result(TypedDict):
    category: Category | None
    section: str | None
    steps: list[Step]
    chars: list[FoundChar]
    code: str | None
    question: Question | None
    missing_required: list[str]
    remarks: list[Remark]
    candidates: list[Candidate]
    options: dict[str, list[str]]
    unknown: list[str]


type FindCandidates = Callable[[str, list[Position]], list[Candidate]]
type FindSection = Callable[[str, Portal], str | None]
type Extract = Callable[[str], list[Fragment]]
type Link = Callable[
    [list[Fragment], list[Candidate], str | None, list[Position], Portal], list[FoundChar]
]
type Narrow = Callable[[list[Candidate], list[FoundChar], list[Position]], Result]
type Run = Callable[[str], Result]
