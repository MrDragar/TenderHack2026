from typing import Literal, NotRequired, TypedDict

from contracts.pipeline import CharSource, FixKind
from contracts.ref import Category, Unit


class ExpectedChar(TypedDict):
    name: str
    value: str | int | float
    unit: NotRequired[Unit | str | None]
    source: NotRequired[CharSource]


class Expected(TypedDict):
    category: Category
    code: str | None
    chars: list[ExpectedChar]


class GoldExample(TypedDict):
    text: str
    expected: Expected


class ExpectedRejected(TypedDict):
    name: str
    value: NotRequired[str]
    reason: NotRequired[str]


class ExpectedCorrection(TypedDict):
    was: str
    now: str
    kind: FixKind


class ExpectedQuestion(TypedDict):
    char: str
    kind: Literal["disambiguation", "required", "confirmation"]


class ExpectedWrite(TypedDict):
    scope: str
    name: str


class ExpectedTurn(TypedDict):
    mode: Literal["ready", "needs_input", "portal_only", "learned_only", "not_found"]
    chars: list[ExpectedChar]
    rejected: list[ExpectedRejected]
    corrections: list[ExpectedCorrection]
    questions: list[ExpectedQuestion]
    writes: list[ExpectedWrite]
    category: NotRequired[Category | None]
    code: NotRequired[str | None]
    corrected_text: NotRequired[str]


class GoldTurn(TypedDict):
    text: str
    learn: NotRequired[bool]
    expected: ExpectedTurn


class GoldScenario(TypedDict):
    id: str
    tags: list[str]
    turns: list[GoldTurn]
