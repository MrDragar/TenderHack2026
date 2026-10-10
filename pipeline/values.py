from contracts.ref import Range, Value

UNIT_ALIASES = {
    "мм": "003",
    "см": "004",
    "м": "006",
    "км": "008",
    "дюйм": "039",
    "г": "163",
    "гр": "163",
    "кг": "166",
    "шт": "796",
    "вт": "212",
    "в": "222",
    "ач": "263",
    "об/мин": "331",
}
# Единицы одной величины переводятся друг в друга через множитель к базовой.
SCALE = {
    "003": ("length", 1),
    "004": ("length", 10),
    "006": ("length", 1000),
    "008": ("length", 1_000_000),
    "039": ("length", 25.4),
    "163": ("mass", 1),
    "166": ("mass", 1000),
}


def matches(value: str | int | float, allowed: Value) -> bool:
    """Текст сравниваем без регистра, число с точным числом КТРУ или диапазоном."""
    if isinstance(value, str) or isinstance(allowed, str):
        return str(value).casefold() == str(allowed).casefold()
    if not isinstance(allowed, dict):
        return value == allowed
    return above_min(value, allowed) and below_max(value, allowed)


def above_min(value: int | float, allowed: Range) -> bool:
    if "min" not in allowed:
        return True
    if allowed.get("min_inclusive"):
        return value >= allowed["min"]
    return value > allowed["min"]


def below_max(value: int | float, allowed: Range) -> bool:
    if "max" not in allowed:
        return True
    if allowed.get("max_inclusive"):
        return value <= allowed["max"]
    return value < allowed["max"]


def bounds(allowed: list[Value]) -> tuple[float, float] | None:
    """Самый широкий числовой коридор КТРУ: точные числа и края диапазонов."""
    edges: list[float] = []
    for value in allowed:
        if isinstance(value, dict):
            edges.append(value["min"] if "min" in value else float("-inf"))
            edges.append(value["max"] if "max" in value else float("inf"))
        elif isinstance(value, int | float):
            edges.append(value)
    if not edges:
        return None
    return min(edges), max(edges)


def show(value: Value) -> str:
    """Значение КТРУ для человека: 48.0 как «48», диапазон как «≥ 16 < 20»."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if not isinstance(value, dict):
        return str(value)
    parts = []
    if "min" in value:
        sign = "≥" if value.get("min_inclusive") else ">"
        parts.append(f"{sign} {show(value['min'])}")
    if "max" in value:
        sign = "≤" if value.get("max_inclusive") else "<"
        parts.append(f"{sign} {show(value['max'])}")
    return " ".join(parts)


def same(a: str | int | float, b: str | int | float) -> bool:
    # В справочнике размер то строка «48», то число 48, сравниваем как показываем.
    return show(a).strip().casefold() == show(b).strip().casefold()


def convert(number: float, from_code: str, to_code: str) -> float:
    return number * scale(from_code)[1] / scale(to_code)[1]


def scale(unit_code: str) -> tuple[str, float]:
    return SCALE.get(unit_code, (unit_code, 1))


def tidy(value: float) -> int | float:
    return int(value) if float(value).is_integer() else round(value, 3)
