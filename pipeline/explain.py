from contracts.pipeline import FixKind, FoundChar, Remark
from pipeline.values import show

WHY: dict[FixKind, str] = {
    "typo": "опечатка, {where} пишется «{value}»",
    "layout": "текст набран не в той раскладке или латиницей, это «{value}»",
    "unit": "{where} единица {unit}, перевели",
    "wrong_unit": (
        "«{fragment}» не бывает у такого товара, похоже, перепутана единица: {value} {unit}"
    ),
    "out_of_range": "вне допустимого по КТРУ: от {low} до {high} {unit}",
    "not_in_list": "такого значения нет в КТРУ, допустимо: {options}",
    "conflict": "в тексте два значения: «{fragment}» и «{other}»",
}
NAME_WHY = {
    "typo": "опечатка в названии товара",
    "layout": "набрано в английской раскладке или латиницей",
}


def mark(char: FoundChar, kind: FixKind, **details: str) -> None:
    """Правило решает только вид правки, текст для человека собирается здесь из уже
    исправленного значения."""
    char["kind"] = kind
    char["fix"] = WHY[kind].format(
        where="в КТРУ" if char["source"] == "ktru" else "на портале",
        value=show(char["value"]),
        unit=char["unit"]["name"].lower() if char["unit"] else "",
        fragment=char["fragment"],
        **details,
    )


def remarks(chars: list[FoundChar]) -> list[Remark]:
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
