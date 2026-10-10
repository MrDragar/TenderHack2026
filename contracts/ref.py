from typing import Literal, NotRequired, TypedDict

type Category = Literal["clothing", "furniture", "tools"]


class Unit(TypedDict):
    code: str
    name: str


class Range(TypedDict):
    min: NotRequired[int | float]
    min_inclusive: NotRequired[bool]
    max: NotRequired[int | float]
    max_inclusive: NotRequired[bool]


type Value = str | int | float | Range


class Characteristic(TypedDict):
    code: str
    name: str
    type: Literal["text", "number"]
    selection: Literal["fixed", "single", "multiple"]
    required: bool
    values: list[Value]
    unit: NotRequired[Unit]


class Position(TypedDict):
    code: str
    name: str
    category: Category
    okpd2_code: str
    position_type: Literal["template", "final"]
    parent_code: str | None
    units: list[Unit]
    characteristics: list[Characteristic]
    description: NotRequired[str]


class PortalChar(TypedDict):
    name: str
    type: str
    unit: str | None
    values: list[str]


type Portal = dict[str, list[PortalChar]]


type LearnedContextKind = Literal["ktru_family", "portal_category", "free_product"]
type LearnedValueType = Literal["text", "number", "boolean"]


class LearnedCharacteristic(TypedDict):
    id: int
    context_kind: LearnedContextKind
    context_key: str
    name: str
    aliases: list[str]
    value_type: LearnedValueType
    unit: Unit | None
    confirmations: int
