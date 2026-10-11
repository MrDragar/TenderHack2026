"""Код это argmax P(код | значения), ответ при P > 1/2, иначе вопрос с наибольшим приростом знания.

Значение, подходящее коду, весит 1 / число его значений; кода без такой характеристики
1 / число значений у соседей; не подходит, ноль. Вопрос выбирается по ожидаемому снижению
энтропии вероятностей кодов.
"""

from math import log2

from contracts.pipeline import Candidate, FoundChar, Narrowed, Question, Step
from contracts.ref import Characteristic, Position
from pipeline.values import matches, show

CATEGORY_NAMES = {"clothing": "Одежда", "furniture": "Мебель", "tools": "Инструменты"}
SHOWN = 4
NO_CHAR = "нет такой характеристики"


def narrow(candidates: list[Candidate], chars: list[FoundChar], ref: list[Position]) -> Narrowed:
    by_code = {p["code"]: p for p in ref}
    family = [by_code[c["code"]] for c in candidates]
    category = family[0]["category"] if family else None
    steps = funnel(family, ref)
    weights = {p["code"]: 1.0 for p in family}
    for found in chars:
        spread = len(family_values(family, found["name"])) or 1
        new = {p["code"]: weights[p["code"]] * likelihood(p, found, spread) for p in family}
        if sum(new.values()) == 0:
            # Значение, которого нет ни у одного кода, не обнуляет выбор: скорее ошибся человек.
            what = f"{found['name']}: {show(found['value'])}, ни у одного кода такого нет, не учли"
            steps.append({"what": what, "left": alive(weights), "ignored": True})
            continue
        if not proportional(weights, new):
            what = f"{found['name']}: {show(found['value'])}"
            steps.append({"what": what, "left": alive(new), "ignored": False})
        weights = new

    total = sum(weights.values())
    posterior = {code: w / total for code, w in weights.items() if w > 0} if total else {}
    left = sorted(
        (p for p in family if p["code"] in posterior), key=lambda p: -posterior[p["code"]]
    )
    best = left[0]["code"] if left else None
    sure = best is not None and posterior[best] > 1 / 2
    return {
        "category": category,
        "steps": steps,
        "code": best if sure else None,
        "question": ask(left, posterior) if left and not sure else None,
        "missing_required": missing_required(left, chars),
        "candidates": [
            {"code": p["code"], "name": p["name"], "score": posterior[p["code"]]} for p in left
        ],
        "options": options(left),
    }


def likelihood(position: Position, found: FoundChar, spread: int) -> float:
    """P(значение | код): равномерно по значениям кода; у кода без этой характеристики
    равномерно по всем значениям семьи, поэтому код, который допускает меньше, выигрывает."""
    char = char_of(position, found["name"])
    if char is None:
        return 1 / spread
    if any(matches(found["value"], v) for v in char["values"]):
        return 1 / len(char["values"])
    return 0.0


def char_of(position: Position, name: str) -> Characteristic | None:
    for char in position["characteristics"]:
        if char["name"] == name and char["values"]:
            return char
    return None


def family_values(family: list[Position], name: str) -> set[str]:
    found = set()
    for position in family:
        char = char_of(position, name)
        if char:
            found.update(show(v) for v in char["values"])
    return found


def alive(weights: dict[str, float]) -> int:
    return sum(w > 0 for w in weights.values())


def proportional(old: dict[str, float], new: dict[str, float]) -> bool:
    ratios = {round(new[c] / w, 12) for c, w in old.items() if w > 0}
    return len(ratios) == 1


def ask(left: list[Position], posterior: dict[str, float]) -> Question:
    """Вопрос, ответ на который в среднем больше всего снижает энтропию вероятностей кодов."""
    names = {c["name"]: c["selection"] for p in left for c in p["characteristics"] if c["values"]}
    best: tuple[float, bool, str] | None = None
    for name, selection in names.items():
        gain = information_gain(left, posterior, name)
        key = (round(gain, 9), selection == "fixed", name)
        if best is None or key > best:
            best = key
    if best is None or best[0] <= 0:
        return {"char": "Код КТРУ", "options": [p["code"] for p in left]}
    return {"char": best[2], "options": sorted(family_values(left, best[2]))}


def information_gain(left: list[Position], posterior: dict[str, float], name: str) -> float:
    """Пользователь называет одно из значений своего кода, любое с равной вероятностью."""
    joint: dict[str, dict[str, float]] = {}
    for position in left:
        char = char_of(position, name)
        answers = [show(v) for v in char["values"]] if char else [NO_CHAR]
        for answer in answers:
            given = joint.setdefault(answer, {})
            given[position["code"]] = posterior[position["code"]] / len(answers)
    after = 0.0
    for given in joint.values():
        mass = sum(given.values())
        after += mass * entropy([w / mass for w in given.values()])
    return entropy(list(posterior.values())) - after


def entropy(probabilities: list[float]) -> float:
    return -sum(p * log2(p) for p in probabilities if p > 0)


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
    """Обязательные характеристики самого вероятного кода, которых нет в тексте."""
    if not left:
        return []
    found_names = {c["name"] for c in chars}
    return [
        c["name"]
        for c in left[0]["characteristics"]
        if c["required"] and c["name"] not in found_names
    ]


def options(left: list[Position]) -> dict[str, list[str]]:
    """Текстовые значения КТРУ оставшихся кодов: из них выбирают при правке и дозаполнении."""
    found: dict[str, set[str]] = {}
    for position in left:
        for char in position["characteristics"]:
            for value in char["values"]:
                if isinstance(value, str):
                    found.setdefault(char["name"], set()).add(value)
    return {name: sorted(values) for name, values in found.items()}
