"""Ветка semantic: новую характеристику вне справочников отбрасываем, если это мусор или чужое.

Перенос отсева из interpret ветки codex/semantic-characteristics: коммерческие названия
(цена, бренд), пустое значение или ссылка, признаки другой категории, а также короткое
название без подсказки «похоже на характеристику» и без слов из названий кандидатов.
"""

import re
import unicodedata

from contracts.pipeline import Candidate, FoundChar, Fragment
from contracts.ref import Category, Portal, Position
from pipeline.link import link as base_link
from pipeline.text import stems, words

GARBAGE_NAMES = {
    "акция",
    "артикул",
    "бренд",
    "вкус",
    "доставка",
    "лучший",
    "продавец",
    "скидка",
    "ссылка",
    "стоимость",
    "цена",
}
CHAR_HINTS = {
    "вес",
    "высо",
    "глуби",
    "диагон",
    "диамет",
    "дли",
    "защел",
    "класс",
    "количе",
    "кром",
    "матери",
    "мощнос",
    "налич",
    "объем",
    "плотно",
    "покры",
    "размер",
    "скорос",
    "состав",
    "тип",
    "толщи",
    "фор",
    "цве",
    "шири",
}
CATEGORY_MARKERS = {
    "clothing": stems("одежда обувь рукав воротник манжета ткань размер ноги"),
    "furniture": stems("столешница каркас ножка сиденье спинка полка фасад тумба"),
    "tools": stems("патрон двигатель лезвие рукоятка сверло удар аккумулятор"),
}


def link(
    fragments: list[Fragment],
    candidates: list[Candidate],
    section: str | None,
    ref: list[Position],
    portal: Portal,
) -> list[FoundChar]:
    found = base_link(fragments, candidates, section, ref, portal)
    category = category_of(candidates, ref)
    product = stems(" ".join(c["name"] for c in candidates))
    kept = []
    for char in found:
        if char["source"] != "new" or not rejected(char, category, product):
            kept.append(char)
    return kept


def category_of(candidates: list[Candidate], ref: list[Position]) -> Category | None:
    codes = {c["code"] for c in candidates}
    for position in ref:
        if position["code"] in codes:
            return position["category"]
    return None


def rejected(char: FoundChar, category: Category | None, product: set[str]) -> bool:
    name_key = normalized(char["name"])
    value = str(char["value"])
    if GARBAGE_NAMES & set(name_key.split()):
        return True
    if not normalized(value) or "http" in value.casefold():
        return True
    char_stems = stems(char["name"])
    if category and foreign(char_stems, category):
        return True
    hinted = char_stems & CHAR_HINTS
    related = char_stems & product
    return not hinted and not related and len(words(char["name"])) < 2


def foreign(char_stems: set[str], category: Category) -> bool:
    others = set()
    for name, markers in CATEGORY_MARKERS.items():
        if name != category:
            others |= markers
    return bool(char_stems & others) and not char_stems & CATEGORY_MARKERS[category]


def normalized(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    return " ".join(re.sub(r"[^\w]+", " ", folded).replace("_", " ").split())
