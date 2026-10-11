"""Самое длинное известное название характеристики в начале фразы, остальное значение.

На dev цена 0.651 → 0.071: верно 12117 → 33077, отказ 22538 → 718, неверно 12 → 872. Словарь
названий КТРУ и листа 2 по основам слов; фраза без названия продолжает значение прошлой пары
(«Цвет розовый, голубой»), названия с запятой ищутся целиком. Почти все неверные это списки
значений, где пункт совпал с названием из листа 2 («ограничитель глубины сверления»).
Не сработали: имена только из двух и более разделов (0.126, больше отказов при тех же
ошибках) и «вся фраза это известное значение, значит продолжение» (0.121: «Цвет черный»
в листе 2 есть как значение). Цена для людей: первая фраза «Стул офисный» тоже становится
парой, а лишнее слово после значения приклеивается к нему.
"""

import re
from functools import cache
from typing import Literal

from contracts.pipeline import Fragment
from pipeline.run import portal, reference
from pipeline.text import PAIR, stem, words
from pipeline.values import UNIT_ALIASES

NUMBER = re.compile(
    r"(?P<number>\d+(?:[.,]\d+)?)(?:\s*(?P<unit>"
    + "|".join(sorted(UNIT_ALIASES, key=len, reverse=True))
    + r")\b)?",
    re.IGNORECASE,
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
    last = None
    for clause in CLAUSE.finditer(text):
        start, end = stripped(text, clause.start(), clause.end())
        if last and start < last["end"]:
            continue
        if start == end or any(s <= start < e for s, e in pairs):
            last = None
            continue
        named = comma_name(text, start) or known_prefix(text, start, end)
        if named:
            found.append(named)
            last = named
        elif last:
            continued(text, last, end)
        else:
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
    for match in NUMBER.finditer(clause):
        if not any(s <= match.start() < e for s, e in spoken):
            item = fragment("number", None, text, start + match.start(), start + match.end())
            item["number"], item["unit"] = number_of(match), unit_code(match)
            found.append(item)
    for item in found:
        item["name"] = text[start : item["start"]].strip() or None
    return found


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
    return float(match["number"].replace(",", "."))


def unit_code(match: re.Match[str]) -> str | None:
    if match["unit"] is None:
        return None
    return UNIT_ALIASES[match["unit"].lower()]


WORD = re.compile(r"\w+")
# Уточнение в скобках «Размер (штихмассовый)» часть названия, даже если его нет в справочнике.
QUALIFIER = re.compile(r"\)*(?:\s*\([^()]*\))?")


@cache
def known_names() -> frozenset[tuple[str, ...]]:
    """Названия характеристик КТРУ и всех разделов портала; с цифрами это значения, а не имена."""
    names = [char["name"] for position in reference() for char in position["characteristics"]]
    names += [char["name"] for chars in portal().values() for char in chars]
    return frozenset(key(name) for name in names if not re.search(r"\d", name))


@cache
def comma_names() -> re.Pattern[str]:
    """Названия с запятой «Толщина столешницы, мм» фраза рвёт, их ищем целиком по тексту."""
    names = [char["name"] for position in reference() for char in position["characteristics"]]
    names += [char["name"] for chars in portal().values() for char in chars]
    names = {" ".join(name.split()) for name in names if "," in name and not re.search(r"\d", name)}
    alternation = "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))
    return re.compile(rf"(?:{alternation})(?=\s+\w)", re.IGNORECASE)


def comma_name(text: str, start: int) -> Fragment | None:
    match = comma_names().match(text, start)
    if not match:
        return None
    clause = CLAUSE.match(text, match.end())
    end = stripped(text, match.end(), clause.end() if clause else match.end())[1]
    item = fragment("pair", match[0], text, start, end)
    set_value(item, text[match.end() : end].strip())
    return item


def key(name: str) -> tuple[str, ...]:
    return tuple(stem(word) for word in WORD.findall(name))


def known_prefix(text: str, start: int, end: int) -> Fragment | None:
    """«Цвет отделки синий»: самое длинное название из справочника, за которым ещё есть
    значение. Сравниваем по основам слов, чтобы ловить формы."""
    clause = text[start:end]
    tokens = list(WORD.finditer(clause))
    stems = [stem(token[0]) for token in tokens]
    names = known_names()
    for count in range(len(tokens) - 1, 0, -1):
        if tuple(stems[:count]) not in names:
            continue
        cut = QUALIFIER.match(clause, tokens[count - 1].end()).end()
        if not WORD.search(clause, cut):
            continue
        item = fragment("pair", clause[:cut], text, start, end)
        set_value(item, clause[cut:].strip())
        return item
    return None


def continued(text: str, item: Fragment, end: int) -> None:
    """Фраза без названия после пары продолжает её значение: «Цвет розовый, голубой»."""
    value_start = item["end"] - len(item["value"])
    item["end"] = end
    item["text"] = text[item["start"] : end]
    set_value(item, text[value_start:end])


def set_value(item: Fragment, value: str) -> None:
    item["value"] = value
    number = NUMBER.search(value)
    if number:
        item["number"], item["unit"] = number_of(number), unit_code(number)
