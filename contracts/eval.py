from typing import TypedDict

from contracts.ref import Category


class ExpectedChar(TypedDict):
    name: str
    value: str | int | float


class Expected(TypedDict):
    category: Category
    code: str | None
    chars: list[ExpectedChar]


class GoldExample(TypedDict):
    text: str
    expected: Expected
