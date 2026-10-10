from collections.abc import Callable
from typing import Literal, NotRequired, TypedDict

from contracts.ref import Category, Portal, Position, Unit


class Candidate(TypedDict):
    code: str
    name: str
    score: float


type FixKind = Literal[
    "typo", "layout", "unit", "wrong_unit", "out_of_range", "not_in_list", "conflict"
]


type Rule = Literal["pair", "value", "number", "gold"]

type Verdict = Literal["property", "unsure", "trash"]


class Check(TypedDict):
    """Свойство товара или мусор. unsure решает модератор. score это вероятность свойства
    по модели, None, если решили без неё. name это единое название из памяти модератора."""

    verdict: Verdict
    why: str
    score: float | None
    name: str | None


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
    rule: Rule
    check: NotRequired[Check]


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


class Narrowed(TypedDict):
    category: Category | None
    steps: list[Step]
    code: str | None
    question: Question | None
    missing_required: list[str]
    candidates: list[Candidate]
    options: dict[str, list[str]]


class Leftover(TypedDict):
    """Кусок описания, который не стал ни товаром, ни характеристикой, либо новая
    характеристика, признанная мусором."""

    text: str
    check: Check


class Decision(TypedDict):
    """Решение модератора о новой характеристике. name это единое название: повторное
    свойство потом ложится в него."""

    told: str
    name: str
    product: str
    approved: bool


class Result(Narrowed):
    section: str | None
    chars: list[FoundChar]
    remarks: list[Remark]
    unknown: list[Leftover]


type FindCandidates = Callable[[str, list[Position]], list[Candidate]]
type FindSection = Callable[[str, Portal], str | None]
type Extract = Callable[[str], list[Fragment]]
type Link = Callable[
    [list[Fragment], list[Candidate], str | None, list[Position], Portal], list[FoundChar]
]
type Narrow = Callable[[list[Candidate], list[FoundChar], list[Position]], Narrowed]
type Run = Callable[[str], Result]
type Judge = Callable[[list[str], str], list[Check]]
# Вероятность, что из посылки следует гипотеза, для каждой пары.
type Entail = Callable[[list[tuple[str, str]]], list[float]]
