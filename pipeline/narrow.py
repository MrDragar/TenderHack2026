from contracts.pipeline import Candidates, FoundChar, Question, Result, Step
from contracts.ref import Position, Range, Value


def narrow(candidates: Candidates, chars: list[FoundChar], ref: list[Position]) -> Result:
    """Отсеивает коды по найденным значениям и решает: код, вопрос или «не нашли»."""
    by_code = {p["code"]: p for p in ref}
    left = [by_code[c["code"]] for c in candidates["candidates"]]
    steps: list[Step] = [first_step(left)]
    for found in chars:
        kept = [p for p in left if fits(p, found)]
        if len(kept) == len(left):
            continue
        value = show(found["value"])
        steps.append({"what": f"{found['name']}: {value}", "left": len(kept)})
        left = kept
        if not left:
            break

    return {
        "category": candidates["category"],
        "steps": steps,
        "chars": chars,
        "code": left[0]["code"] if len(left) == 1 else None,
        "question": ask(left) if len(left) > 1 else None,
        "missing_required": missing_required(left, chars),
        "remarks": [],
    }


def first_step(left: list[Position]) -> Step:
    if not left:
        return {"what": "Не нашли в КТРУ позицию с похожим названием", "left": 0}
    names = ", ".join(sorted({p["name"] for p in left}))
    return {"what": f"Похоже на {names}", "left": len(left)}


def missing_required(left: list[Position], chars: list[FoundChar]) -> list[str]:
    """Обязательные характеристики первого оставшегося кода, которых нет в тексте."""
    if not left:
        return []
    found_names = {c["name"] for c in chars}
    missing = []
    for char in left[0]["characteristics"]:
        if char["required"] and char["name"] not in found_names:
            missing.append(char["name"])
    return missing


def fits(position: Position, found: FoundChar) -> bool:
    """Код отпадает, только если характеристика у него неизменяемая и значение не подходит."""
    for char in position["characteristics"]:
        if char["name"] == found["name"] and char["selection"] == "fixed":
            return any(matches(found["value"], v) for v in char["values"])
    return True


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


def ask(left: list[Position]) -> Question:
    """Вопрос про неизменяемую характеристику, сильнее всего делящую оставшиеся коды."""
    options: dict[str, set[str]] = {}
    for position in left:
        for char in position["characteristics"]:
            if char["selection"] == "fixed":
                values = ", ".join(show(v) for v in char["values"])
                options.setdefault(char["name"], set()).add(values)
    name, values = max(options.items(), key=lambda item: len(item[1]), default=("", set()))
    if len(values) < 2:
        return {"char": "Код КТРУ", "options": [p["code"] for p in left]}
    return {"char": name, "options": sorted(values)}


def show(value: Value) -> str:
    """Значение КТРУ для человека, диапазон как «≥ 16 < 20»."""
    if not isinstance(value, dict):
        return str(value)
    parts = []
    if "min" in value:
        sign = "≥" if value.get("min_inclusive") else ">"
        parts.append(f"{sign} {value['min']}")
    if "max" in value:
        sign = "≤" if value.get("max_inclusive") else "<"
        parts.append(f"{sign} {value['max']}")
    return " ".join(parts)
