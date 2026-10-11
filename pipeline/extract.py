import re
from typing import Literal

from contracts.pipeline import Fragment
from pipeline.text import PAIR, words
from pipeline.values import UNIT_ALIASES

UNIT = "|".join(sorted(map(re.escape, UNIT_ALIASES), key=len, reverse=True))
# Цифры, приклеенные к буквам, это модель или артикул: «DF032DWME», «ДУ-20880Т», «X11».
NUMBER = re.compile(
    r"(?<!\w)(?P<number>\d+(?:[.,]\d+)?)(?:\s*(?P<unit>" + UNIT + r")(?!\w))?(?!\w)",
    re.IGNORECASE,
)
SIDE = r"(\d+(?:[.,]\d+)?)"
BY = r"\s*(?:[хx×*]|на)\s*"
SIZE = re.compile(
    SIDE + BY + SIDE + rf"(?:{BY}{SIDE})?(?:\s*(?P<unit>{UNIT})(?!\w))?", re.IGNORECASE
)
TENS = {
    "десять": 10,
    "двадцать": 20,
    "тридцать": 30,
    "сорок": 40,
    "пятьдесят": 50,
    "шестьдесят": 60,
    "семьдесят": 70,
    "восемьдесят": 80,
    "девяносто": 90,
}
# «Метр десять» так говорят о росте и высоте: метр и сантиметры словом.
SPOKEN = re.compile(r"\bметр[а]?\s+(?P<tens>" + "|".join(TENS) + r")\b", re.IGNORECASE)
# Запятая между цифрами это дробь «2,2 см», точка без пробела после тоже не конец фразы.
CLAUSE = re.compile(r"(?:[^,;\n.]|(?<=\d),(?=\d)|\.(?!\s))+")


def extract(text: str) -> list[Fragment]:
    """Куски текста о свойствах товара как написаны, без справочника: что это за свойство, решает
    привязка. Кусок «words» это вся фраза без явной пары, значения без названия ищутся в ней."""
    found = []
    pairs = []
    for match in PAIR.finditer(text):
        found.append(pair(text, match))
        pairs.append((match.start("name"), match.end("value")))
    for clause in CLAUSE.finditer(text):
        start, end = stripped(text, clause.start(), clause.end())
        if start == end or any(s <= start < e for s, e in pairs):
            continue
        found.extend(numbers(text, start, end))
        if words(text[start:end]):
            found.append(fragment("words", None, text, start, end))
    found.sort(key=lambda f: f["start"])
    return found


def pair(text: str, match: re.Match[str]) -> Fragment:
    """Явное «Тип каркаса: Деревянный»: так пишет человек, ответ на вопрос и правка таблицы."""
    start, end = stripped(text, match.start("name"), match.end("value"))
    item = fragment("pair", match["name"].strip(), text, start, end)
    item["value"] = match["value"].strip()
    number = NUMBER.search(item["value"])
    if number:
        item["number"], item["unit"] = number_of(number), unit_code(number)
    return item


def numbers(text: str, start: int, end: int) -> list[Fragment]:
    """Числа фразы, название — слова перед числом: по ним привязка выберет характеристику."""
    clause = text[start:end]
    found = []
    spoken = []
    for match in SPOKEN.finditer(clause):
        spoken.append((match.start(), match.end()))
        item = fragment("number", None, text, start + match.start(), start + match.end())
        item["number"], item["unit"] = 1 + TENS[match["tens"].lower()] / 100, "006"
        found.append(item)
    for match in SIZE.finditer(clause):
        found += sides(text, start, match)
        spoken.append((match.start(), match.end()))
    for match in NUMBER.finditer(clause):
        if not any(s <= match.start() < e for s, e in spoken):
            item = fragment("number", None, text, start + match.start(), start + match.end())
            item["number"], item["unit"] = number_of(match), unit_code(match)
            found.append(item)
    for item in found:
        if item["name"] is None:
            # «4 полки», «2 выдвижных ящика»: название после числа, если перед ним ничего нет.
            before = text[start : item["start"]].strip()
            item["name"] = before or text[item["end"] : end].strip() or None
    return found


def sides(text: str, start: int, match: re.Match[str]) -> list[Fragment]:
    """«1200х800 мм», «90 на 200»: длина это большая сторона, ширина меньшая, третья высота.
    Порядок записи у людей разный, а длина по определению наибольший размер."""
    groups = [group for group in (1, 2, 3) if match[group] is not None]
    long = number_of_text(match[1]) >= number_of_text(match[2])
    names = ["длина", "ширина"] if long else ["ширина", "длина"]
    before = text[start : start + match.start()].strip()
    found = []
    for group, name in zip(groups, [*names, "высота"], strict=False):
        item = fragment("number", None, text, start + match.start(group), start + match.end(group))
        item["name"] = f"{before} {name}".strip()
        item["number"], item["unit"] = number_of_text(match[group]), unit_code(match)
        found.append(item)
    return found


def number_of_text(number: str) -> float:
    return float(number.replace(",", "."))


def fragment(
    form: Literal["pair", "number", "words"], name: str | None, text: str, start: int, end: int
) -> Fragment:
    return {
        "form": form,
        "name": name,
        "value": text[start:end],
        "text": text[start:end],
        "number": None,
        "unit": None,
        "start": start,
        "end": end,
    }


def stripped(text: str, start: int, end: int) -> tuple[int, int]:
    piece = text[start:end]
    left = len(piece) - len(piece.lstrip())
    return start + left, start + len(piece.rstrip())


def number_of(match: re.Match[str]) -> float:
    return number_of_text(match["number"])


def unit_code(match: re.Match[str]) -> str | None:
    if match["unit"] is None:
        return None
    return UNIT_ALIASES[match["unit"].lower()]
