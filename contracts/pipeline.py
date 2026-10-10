from collections.abc import Callable
from typing import Literal, NotRequired, TypedDict

from contracts.ref import Category, LearnedCharacteristic, Portal, Position, Unit


class Candidate(TypedDict):
    code: str
    name: str
    score: float


class PortalCandidate(TypedDict):
    name: str
    score: float


class Candidates(TypedDict):
    category: Category | None
    candidates: list[Candidate]
    portal_categories: NotRequired[list[PortalCandidate]]


type FixKind = Literal[
    "typo", "layout", "unit", "wrong_unit", "out_of_range", "not_in_list", "conflict"
]
type CharSource = Literal["ktru", "portal", "new", "learned"]
type CharStatus = Literal["accepted", "corrected", "needs_confirmation", "rejected"]


class FoundChar(TypedDict):
    name: str
    value: str | int | float
    unit: Unit | None
    source: CharSource
    fragment: str
    start: int
    end: int
    required: bool
    fix: str | None
    kind: FixKind | None
    status: NotRequired[CharStatus]
    confidence: NotRequired[float]
    learned_now: NotRequired[bool]


class Step(TypedDict):
    what: str
    left: int
    ignored: bool


class Question(TypedDict):
    char: str
    options: list[str]
    kind: NotRequired[Literal["disambiguation", "required", "confirmation"]]
    reason: NotRequired[str]


class Remark(TypedDict):
    was: str
    now: str
    why: str
    kind: FixKind


class TextEdit(TypedDict):
    start: int
    end: int
    was: str
    now: str
    why: str
    kind: FixKind


class RejectedFragment(TypedDict):
    fragment: str
    start: int
    end: int
    reason: str
    kind: Literal["garbage", "irrelevant", "unmatched", "invalid"]


class LearningDecision(TypedDict):
    name: str
    action: Literal["pending", "created", "updated", "reused", "rejected", "failed"]
    context: str
    reason: NotRequired[str]


class Result(TypedDict):
    category: Category | None
    steps: list[Step]
    chars: list[FoundChar]
    code: str | None
    question: Question | None
    missing_required: list[str]
    remarks: list[Remark]
    candidates: list[Candidate]
    options: dict[str, list[str]]
    unknown: list[str]
    status: NotRequired[Literal["ready", "needs_input", "portal_only", "learned_only", "not_found"]]
    portal_category: NotRequired[str | None]
    questions: NotRequired[list[Question]]
    rejected: NotRequired[list[RejectedFragment]]
    edits: NotRequired[list[TextEdit]]
    corrected_text: NotRequired[str]
    learning: NotRequired[list[LearningDecision]]
    implementation: NotRequired[str]


class Knowledge(TypedDict):
    positions: list[Position]
    portal: Portal
    learned: list[LearnedCharacteristic]


class Interpretation(TypedDict):
    chars: list[FoundChar]
    rejected: list[RejectedFragment]
    portal_category: str | None


type Retrieve = Callable[[str, Knowledge], Candidates]
type Interpret = Callable[[str, Candidates, Knowledge], Interpretation]
type Decide = Callable[[Candidates, Interpretation, Knowledge], Result]


class PipelineSteps(TypedDict):
    retrieve: Retrieve
    interpret: Interpret
    decide: Decide


type FindCandidates = Callable[[str, list[Position]], Candidates]
type ExtractChars = Callable[[str, Candidates, list[Position], Portal], list[FoundChar]]
type Narrow = Callable[[Candidates, list[FoundChar], list[Position]], Result]
type Run = Callable[[str], Result]
