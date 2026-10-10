from collections import Counter
from math import log2

from contracts.pipeline import Candidates, FoundChar, Question, Remark, Result, Step
from contracts.ref import Position
from pipeline.values import matches, show

CATEGORY_NAMES = {"clothing": "Одежда", "furniture": "Мебель", "tools": "Инструменты"}
SHOWN = 4


def narrow(candidates: Candidates, chars: list[FoundChar], ref: list[Position]) -> Result:
    """Отсеивает коды по найденным значениям и решает: код, вопрос или «не нашли»."""
    by_code = {p["code"]: p for p in ref}
    left = [by_code[c["code"]] for c in candidates["candidates"]]
    steps = funnel(left, ref)
    for found in chars:
        kept = [p for p in left if fits(p, found)]
        if len(kept) == len(left):
            continue
        what = f"{found['name']}: {show(found['value'])}"
        if not kept:
            # Значение, которого нет ни у одного кода, не обнуляет выбор: скорее ошибся человек.
            what += ", ни у одного кода такого нет, не учли"
            steps.append({"what": what, "left": len(left), "ignored": True})
            continue
        steps.append({"what": what, "left": len(kept), "ignored": False})
        left = kept

    return {
        "category": candidates["category"],
        "steps": steps,
        "chars": chars,
        "code": left[0]["code"] if len(left) == 1 else None,
        "question": ask(left) if len(left) > 1 else None,
        "missing_required": missing_required(left, chars),
        "remarks": remarks(chars),
        "candidates": [
            {"code": p["code"], "name": p["name"], "score": 1 / len(left)} for p in left
        ],
        "options": options(left),
        "unknown": [],
    }


def remarks(chars: list[FoundChar]) -> list[Remark]:
    """Правка видна пользователю, если значение взято не как есть."""
    found: list[Remark] = []
    for char in chars:
        if char["fix"] is None or char["kind"] is None:
            continue
        now = show(char["value"])
        if char["unit"]:
            now = f"{now} {char['unit']['name'].lower()}"
        found.append(
            {
                "was": char["fragment"],
                "now": f"{char['name']}: {now}",
                "why": char["fix"],
                "kind": char["kind"],
            }
        )
    return found


def funnel(left: list[Position], ref: list[Position]) -> list[Step]:
    """Сужение сверху вниз: категория, группа ОКПД2, позиция КТРУ, как устроен сам справочник."""
    if not left:
        return [
            {"what": "Не нашли в КТРУ позицию с похожим названием", "left": 0, "ignored": False}
        ]
    by_code = {p["code"]: p for p in ref}
    category = left[0]["category"]
    groups = sorted({p["okpd2_code"] for p in left})
    in_category = [p for p in ref if p["position_type"] == "final" and p["category"] == category]
    in_groups = [p for p in in_category if p["okpd2_code"] in groups]
    names = sorted({template_name(p, by_code) for p in left})
    return [
        {
            "what": f"Категория: {CATEGORY_NAMES[category]}",
            "left": len(in_category),
            "ignored": False,
        },
        {"what": f"Группа ОКПД2: {listed(groups)}", "left": len(in_groups), "ignored": False},
        {"what": f"Позиция КТРУ: {listed(names)}", "left": len(left), "ignored": False},
    ]


def template_name(position: Position, by_code: dict[str, Position]) -> str:
    parent = by_code.get(position["parent_code"] or "")
    return parent["name"] if parent else position["name"]


def listed(items: list[str]) -> str:
    if len(items) <= SHOWN:
        return ", ".join(items)
    return f"{', '.join(items[:SHOWN])} и ещё {len(items) - SHOWN}"


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


def options(left: list[Position]) -> dict[str, list[str]]:
    """Текстовые значения КТРУ оставшихся кодов: из них выбирают при правке и дозаполнении."""
    found: dict[str, set[str]] = {}
    for position in left:
        for char in position["characteristics"]:
            for value in char["values"]:
                if isinstance(value, str):
                    found.setdefault(char["name"], set()).add(value)
    return {name: sorted(values) for name, values in found.items()}


def fits(position: Position, found: FoundChar) -> bool:
    """Код отпадает, только если характеристика у него неизменяемая и значение не подходит."""
    for char in position["characteristics"]:
        if char["name"] == found["name"] and char["selection"] == "fixed":
            return any(matches(found["value"], v) for v in char["values"])
    return True


def ask(left: list[Position]) -> Question:
    """Вопрос, ответ на который в среднем сильнее всего сужает выбор: максимум энтропии."""
    groups: dict[str, Counter[str]] = {}
    for position in left:
        for char in position["characteristics"]:
            if char["selection"] == "fixed":
                values = ", ".join(show(v) for v in char["values"])
                groups.setdefault(char["name"], Counter())[values] += 1
    best = max(groups.items(), key=lambda item: entropy(item[1]), default=None)
    if best is None or len(best[1]) < 2:
        return {"char": "Код КТРУ", "options": [p["code"] for p in left]}
    return {"char": best[0], "options": sorted(best[1])}


def entropy(counts: Counter[str]) -> float:
    total = sum(counts.values())
    return -sum(n / total * log2(n / total) for n in counts.values())
