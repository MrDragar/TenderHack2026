from typing import NotRequired, TypedDict

from contracts.pipeline import FixKind
from contracts.ref import Category


class ExpectedChar(TypedDict):
    name: str
    value: str | int | float
    kind: NotRequired[FixKind]


class Expected(TypedDict):
    category: Category
    code: str | None
    chars: list[ExpectedChar]


class GoldExample(TypedDict):
    text: str
    expected: Expected
