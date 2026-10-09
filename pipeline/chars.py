import re
from typing import TypedDict

from contracts.pipeline import Candidates, FoundChar
from contracts.ref import Characteristic, Portal, Position
from pipeline.text import stems, words

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
NUMBER = re.compile(
    r"(?P<number>\d+(?:[.,]\d+)?)\s*(?P<unit>"
    + "|".join(sorted(UNIT_ALIASES, key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)


class Match(TypedDict):
    char: str
    value: str
    start: int
    end: int
    value_words: int
    name_words: int


def extract(
    text: str, candidates: Candidates, ref: list[Position], portal: Portal
) -> list[FoundChar]:
    by_code = {p["code"]: p for p in ref}
    chars = []
    for candidate in candidates["candidates"]:
        chars.extend(by_code[candidate["code"]]["characteristics"])
    return text_values(text, chars) + number_values(text, chars)


def text_values(text: str, chars: list[Characteristic]) -> list[FoundChar]:
    """Текстовые значения КТРУ из текста: «деревянный» → Тип каркаса: Деревянный."""
    best: dict[str, Match] = {}
    for match in best_per_fragment(find_matches(text, chars)):
        kept = best.get(match["char"])
        # Из двух значений одной характеристики берём более полное: «Металл хромированный».
        if kept is None or match["value_words"] > kept["value_words"]:
            best[match["char"]] = match

    found: list[FoundChar] = []
    for match in best.values():
        fragment = text[match["start"] : match["end"]]
        found.append(
            {
                "name": match["char"],
                "value": match["value"],
                "unit": None,
                "source": "ktru",
                "fragment": fragment,
            }
        )
    return found


def find_matches(text: str, chars: list[Characteristic]) -> list[Match]:
    """Все значения кандидатов, каждое слово которых есть в тексте, без выбора."""
    text_words = words(text)
    text_stems = {word["stem"] for word in text_words}
    matches: list[Match] = []
    for char in chars:
        name_words = len(stems(char["name"]) & text_stems)
        for value in char["values"]:
            if not isinstance(value, str) or value == "Нет":
                continue
            wanted = stems(searched_text(char, value))
            hits = [word for word in text_words if word["stem"] in wanted]
            if not wanted or {word["stem"] for word in hits} != wanted:
                continue
            matches.append(
                {
                    "char": char["name"],
                    "value": value,
                    "start": min(word["start"] for word in hits),
                    "end": max(word["end"] for word in hits),
                    "value_words": len(wanted),
                    "name_words": name_words,
                }
            )
    return matches


def searched_text(char: Characteristic, value: str) -> str:
    """«Да» ищем по названию: «из натуральной кожи» значит «Натуральная кожа: Да»."""
    if value == "Да":
        return char["name"]
    return value


def best_per_fragment(matches: list[Match]) -> list[Match]:
    """Кусок текста одной характеристике: «ЛДСП» у столешницы, если она есть в тексте."""
    by_fragment: dict[tuple[int, int], list[Match]] = {}
    for match in matches:
        by_fragment.setdefault((match["start"], match["end"]), []).append(match)

    kept = []
    for group in by_fragment.values():
        top = max(strength(match) for match in group)
        for match in group:
            if strength(match) == top:
                kept.append(match)
    return kept


def strength(match: Match) -> tuple[int, int]:
    """Сначала полнота значения, затем сколько слов названия характеристики есть в тексте."""
    return match["value_words"], match["name_words"]


def number_values(text: str, chars: list[Characteristic]) -> list[FoundChar]:
    """Числа с единицей в единице КТРУ: «2,2 см» → Толщина столешницы 22 мм."""
    numeric = {}
    for char in chars:
        if char["type"] == "number" and "unit" in char:
            numeric[char["name"]] = char

    found: list[FoundChar] = []
    for match in NUMBER.finditer(text):
        unit_code = UNIT_ALIASES[match["unit"].lower()]
        # Какой характеристике принадлежит число, решают слова перед ним до запятой.
        clause = re.split(r"[,;\n]", text[: match.start()])[-1]
        char = owner(list(numeric.values()), unit_code, stems(clause))
        if char is None:
            continue
        unit = char["unit"]
        number = float(match["number"].replace(",", "."))
        value = convert(number, unit_code, unit["code"])
        found.append(
            {
                "name": char["name"],
                "value": int(value) if value.is_integer() else round(value, 3),
                "unit": unit,
                "source": "ktru",
                "fragment": match[0],
            }
        )
    return found


def owner(
    chars: list[Characteristic], unit_code: str, clause_stems: set[str]
) -> Characteristic | None:
    """Характеристика той же величины, чьё название ближе к словам перед числом."""
    dimension = scale(unit_code)[0]
    best = None
    best_hits = 0
    same_dimension = 0
    for char in chars:
        if scale(char["unit"]["code"])[0] != dimension:
            continue
        same_dimension += 1
        hits = len(stems(char["name"]) & clause_stems)
        if best is None or hits > best_hits:
            best = char
            best_hits = hits
    # Без подсказки в тексте угадываем, только если выбора нет.
    if best_hits == 0 and same_dimension > 1:
        return None
    return best


def convert(number: float, from_code: str, to_code: str) -> float:
    return number * scale(from_code)[1] / scale(to_code)[1]


def scale(unit_code: str) -> tuple[str, float]:
    return SCALE.get(unit_code, (unit_code, 1))
