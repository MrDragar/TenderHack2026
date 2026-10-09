import re

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
    r"(\d+(?:[.,]\d+)?)\s*(" + "|".join(sorted(UNIT_ALIASES, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def extract(
    text: str, candidates: Candidates, ref: list[Position], portal: Portal
) -> list[FoundChar]:
    by_code = {p["code"]: p for p in ref}
    chars = []
    for candidate in candidates["candidates"]:
        chars.extend(by_code[candidate["code"]]["characteristics"])
    return text_values(text, chars) + number_values(text, chars)


def text_values(text: str, chars: list[Characteristic]) -> list[FoundChar]:
    tokens = words(text)
    text_stems = {s for _, _, s in tokens}
    # Фрагмент текста -> лучшие пары (характеристика, значение) для него.
    matches: dict[tuple[int, int], list[tuple[int, int, str, str]]] = {}
    for char in chars:
        for value in char["values"]:
            if not isinstance(value, str) or value == "Нет":
                continue
            # «Да» ищем по названию: «из натуральной кожи» значит «Натуральная кожа: Да».
            value_stems = stems(char["name"] if value == "Да" else value)
            hit = [t for t in tokens if t[2] in value_stems]
            if not value_stems or {t[2] for t in hit} != value_stems:
                continue
            span = (min(t[0] for t in hit), max(t[1] for t in hit))
            name_hits = len(stems(char["name"]) & text_stems)
            matches.setdefault(span, []).append((len(value_stems), name_hits, char["name"], value))

    best: dict[str, tuple[int, FoundChar]] = {}
    for span, options in matches.items():
        top = max((size, name_hits) for size, name_hits, _, _ in options)
        for size, name_hits, name, value in options:
            if (size, name_hits) != top or best.get(name, (0,))[0] >= size:
                continue
            found: FoundChar = {
                "name": name,
                "value": value,
                "unit": None,
                "source": "ktru",
                "fragment": text[span[0] : span[1]],
            }
            best[name] = (size, found)
    return [found for _, found in best.values()]


def number_values(text: str, chars: list[Characteristic]) -> list[FoundChar]:
    numeric = {c["name"]: c for c in chars if c["type"] == "number" and "unit" in c}
    found: list[FoundChar] = []
    for m in NUMBER.finditer(text):
        unit_code = UNIT_ALIASES[m[2].lower()]
        dimension, factor = scale(unit_code)
        options = [c for c in numeric.values() if scale(c["unit"]["code"])[0] == dimension]
        # Какой характеристике принадлежит число, решают слова перед ним до запятой.
        before = stems(re.split(r"[,;\n]", text[: m.start()])[-1])
        scored = [(len(stems(c["name"]) & before), c) for c in options]
        if not scored:
            continue
        hits, char = max(scored, key=lambda pair: pair[0])
        if hits == 0 and len(scored) > 1:
            continue
        unit = char["unit"]
        value = float(m[1].replace(",", ".")) * factor / scale(unit["code"])[1]
        found.append(
            {
                "name": char["name"],
                "value": int(value) if value.is_integer() else round(value, 3),
                "unit": unit,
                "source": "ktru",
                "fragment": m[0],
            }
        )
    return found


def scale(unit_code: str) -> tuple[str, float]:
    return SCALE.get(unit_code, (unit_code, 1))
