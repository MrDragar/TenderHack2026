from contracts.ref import Range, Value


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
