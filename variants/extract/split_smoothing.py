"""Двоеточие из справочника часть названия, слова сглажены Кнесером-Неем по форме слова.

Часть 1, двоеточие: dev 26856 верно / 649 отказ / 280 неверно (0.044) → 26981 / 517 / 287
(0.039). Ушли все 132 отказа на названиях с двоеточием («Длина:» 56, «Гарантийный срок:» 14),
125 стали верными, 7 неверными: значение-список после двоеточия обрывается на запятой.
Часть 2, Кнесер-Ней «значения названия → в скольких названиях было слово → класс по форме
(латиница, артикул, кириллица, длина)» вместо Виттена-Белла, латиница с цифрами одно слово:
26977 / 512 / 296 (0.040), то есть чуть хуже одной части 1. Внутри большая перетасовка:
124 пары стали верными (бренды и серии латиницей «Mary Poppins», «Santoro London», «BLITZ»,
длинные списки после запятой), 142 испортились: незнакомое русское значение «Материал Грета»,
«Узор по заказу» теперь отказ, потому что среди редких слов фона почти одна кириллица и класс
тянет такое слово в фон; списки «ручкой, замком» стали обрываться. Класс по форме помогает
латинице ровно настолько, насколько вредит кириллице. Лучший вариант это часть 1 без части 2
(копия в data/eval/runs/20261011-042908-extract-split_smoothing/variant.py).
"""

import math
import re
from collections import Counter
from functools import cache
from typing import Literal

from contracts.pipeline import Fragment
from pipeline.run import portal, reference
from pipeline.text import PAIR, words
from pipeline.values import UNIT_ALIASES, show

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
SPOKEN = re.compile(r"\bметр[а]?\s+(?P<tens>" + "|".join(TENS) + r")\b", re.IGNORECASE)
CLAUSE = re.compile(r"(?:[^,;\n.]|(?<=\d),(?=\d)|\.(?!\s))+")
# Латинское слово с цифрами одно слово: так пишут артикулы «DCD771C2».
TOKEN = re.compile(r"[a-z][a-z\d]*|\d+(?:[.,]\d+)?|[^\W\d_]+")
SPAN = 6


def extract(text: str) -> list[Fragment]:
    found = []
    pairs = []
    for match in PAIR.finditer(text):
        found.append(pair(text, match))
        pairs.append((match.start("name"), match.end("value")))
    runs: list[list[tuple[int, int]]] = [[]]
    for clause in CLAUSE.finditer(text):
        start, end = stripped(text, clause.start(), clause.end())
        if start == end:
            continue
        if any(s <= start < e for s, e in pairs):
            runs.append([])
            continue
        runs[-1].append((start, end))
        found.extend(numbers(text, start, end))
        if words(text[start:end]):
            found.append(fragment("words", None, text, start, end))
    for run in runs:
        found.extend(segment(text, run))
    found.sort(key=lambda f: f["start"])
    return found


def segment(text: str, clauses: list[tuple[int, int]]) -> list[Fragment]:
    """Витерби по фразам: каждая либо просто фраза, либо начало пары, чьё значение может идти
    через запятую «Цвет розовый, голубой», а название через запятую, если таким его и пишут."""
    best: list[tuple[float, list[tuple[int, int, int]]]] = [(0.0, [])]
    for j in range(len(clauses)):
        score, cuts = best[j]
        options = [(score, cuts)]
        for i in range(max(0, j + 1 - SPAN), j + 1):
            cut, gain = best_cut(text, clauses[i][0], clauses[j][1])
            if cut is not None:
                gain += (j - i) * model()["comma"]
                options.append(
                    (best[i][0] + gain, [*best[i][1], (clauses[i][0], cut, clauses[j][1])])
                )
        best.append(max(options, key=lambda option: option[0]))
    return [split(text, start, cut, end) for start, cut, end in best[-1][1]]


@cache
def best_cut(text: str, start: int, end: int) -> tuple[int | None, float]:
    """Разрез с наибольшим отношением правдоподобия к «просто фразе»; нет выигрыша, нет разреза."""
    best, best_gain = None, 0.0
    for match in re.finditer(r"(?<=\s)\S", text[start:end]):
        cut = start + match.start()
        name = text[start:cut].strip()
        gain = name_gain(name) + value_gain(text[cut:end], name)
        if gain > best_gain:
            best, best_gain = cut, gain
    return best, best_gain


@cache
def name_gain(name: str) -> float:
    stats = model()
    tokens = tokenize(name)
    count = stats["names"].get(name.casefold(), 0)
    if count:
        likely = math.log(count / stats["names_total"])
    else:
        likely = math.log(stats["new_name"]) + sum(math.log(prob(t, "name_words")) for t in tokens)
    return likely - sum(math.log(prob(t, "background")) for t in tokens)


@cache
def value_gain(value: str, name: str) -> float:
    """Слова значения из словаря этого названия, незнакомое слово из общего словаря значений с
    долей, как часто у названия встречались новые слова: у «Бренда» почти всегда."""
    own = model()["values"].get(name.casefold(), Counter())
    total = own.total()
    gain = 0.0
    for token in tokenize(value):
        lower = prob(token, "value_words")
        likely = kneser_ney(own, total, discount(name.casefold()), token, lower)
        gain += math.log(likely) - math.log(prob(token, "background"))
    return gain


@cache
def discount(name: str) -> float:
    return estimate(model()["values"].get(name, Counter()))


@cache
def prob(token: str, kind: str) -> float:
    """Интерполированный Кнесер-Ней с откатом на класс слова по форме: незнакомый бренд
    латиницей вероятен там, где среди редких слов много латиницы, то есть в значениях."""
    stats = model()
    counts = stats[kind]
    total = stats["totals"][kind]
    return kneser_ney(counts, total, stats["discounts"][kind], token, by_shape(token, kind))


def kneser_ney(counts: Counter[str], total: int, cut: float, token: str, lower: float) -> float:
    if not total:
        return lower
    return max(counts[token] - cut, 0) / total + cut * len(counts) / total * lower


def by_shape(token: str, kind: str) -> float:
    stats = model()
    rare = stats["rare"][kind]
    share = (rare[shape(token)] + 1) / (rare.total() + len(stats["shapes"]) + 1)
    return share / (stats["shapes"][shape(token)] + 1)


def estimate(counts: Counter[str]) -> float:
    """Скидка Ней по числу слов, встреченных один и два раза; без них середина."""
    once = sum(1 for count in counts.values() if count == 1)
    twice = sum(1 for count in counts.values() if count == 2)
    if not once or not twice:
        return 0.5
    return min(max(once / (once + 2 * twice), 0.1), 0.9)


def shape(token: str) -> str:
    """Класс незнакомого слова как в моделях незнакомых слов: алфавит, цифры, длина."""
    if token == "<num>":
        return token
    if token.isascii():
        script = "mixed" if any(c.isdigit() for c in token) else "latin"
    else:
        script = "cyrillic"
    return f"{script}{min(len(token), 6) // 3}"


def tokenize(text: str) -> list[str]:
    found = []
    for token in TOKEN.findall(text.casefold().replace("ё", "е")):
        found.append("<num>" if token[0].isdigit() else token)
    return found


@cache
def model() -> dict:
    """Частоты названий и слов значений по разделам листа 2 и позициям КТРУ. Фон, то есть
    «просто фраза», это слова названий разделов и позиций: так пишут о самом товаре."""
    names: Counter[str] = Counter()
    values: dict[str, Counter[str]] = {}
    background: Counter[str] = Counter()
    commas = 0
    portal_values = 0
    for section, chars in portal().items():
        background.update(tokenize(section))
        for char in chars:
            portal_values += len(char["values"])
            commas += sum(value.count(",") for value in char["values"])
            listed = list(char["values"])
            # Числовые значения в листе 2 не перечислены, но их вид «число и единица» известен.
            if "числов" in char["type"]:
                listed.append(f"1 {char['unit'] or ''}")
            seen(names, values, char["name"], listed)
    for position in reference():
        background.update(tokenize(position["name"]))
        for char in position["characteristics"]:
            listed = [show(v) for v in char["values"]]
            if char["type"] == "number":
                listed.append(f"1 {char.get('unit', {}).get('name', '')}")
            seen(names, values, char["name"], listed)
    name_words: Counter[str] = Counter()
    for name, count in names.items():
        for token in tokenize(name):
            name_words[token] += count
    # Нижний уровень Кнесера-Нея: в скольких словарях названий встретилось слово.
    value_words: Counter[str] = Counter()
    for counts in values.values():
        value_words.update(counts.keys())
    kinds = {"name_words": name_words, "value_words": value_words, "background": background}
    singles = sum(1 for count in names.values() if count == 1)
    return {
        "names": names,
        "names_total": names.total(),
        "new_name": singles / names.total(),
        "values": values,
        "name_words": name_words,
        "value_words": value_words,
        "background": background,
        "discounts": {kind: estimate(counts) for kind, counts in kinds.items()},
        "totals": {kind: counts.total() for kind, counts in kinds.items()},
        "rare": {
            kind: Counter(shape(t) for t, count in counts.items() if count == 1)
            for kind, counts in kinds.items()
        },
        "shapes": Counter(shape(t) for t in name_words | value_words | background),
        # Цена запятой внутри пары: сколько запятых приходится на одно значение листа 2.
        "comma": math.log(commas / portal_values),
    }


def seen(
    names: Counter[str], values: dict[str, Counter[str]], name: str, listed: list[str]
) -> None:
    names[name.casefold()] += 1
    counts = values.setdefault(name.casefold(), Counter())
    for value in listed:
        counts.update(tokenize(value))


def split(text: str, start: int, cut: int, end: int) -> Fragment:
    item = fragment("pair", text[start:cut].strip(), text, start, end)
    item["value"] = text[cut:end].strip()
    number = NUMBER.search(item["value"])
    if number:
        item["number"], item["unit"] = number_of(number), unit_code(number)
    return item


def pair(text: str, match: re.Match[str]) -> Fragment:
    start, end = stripped(text, match.start("name"), match.end("value"))
    name, value = colon_name(text[start:end]) or (match["name"].strip(), match["value"].strip())
    item = fragment("pair", name, text, start, end)
    item["value"] = value
    number = NUMBER.search(item["value"])
    if number:
        item["number"], item["unit"] = number_of(number), unit_code(number)
    return item


def colon_name(piece: str) -> tuple[str, str] | None:
    """Двоеточие часть названия, если справочник знает название вместе с ним: в листе 2
    есть «Длина:» и «Состав ткани: хлопок», и так их пишут в листе 1."""
    tokens = piece.split()
    for size in range(len(tokens) - 1, 0, -1):
        name = " ".join(tokens[:size])
        if ":" in name and name.casefold() in model()["names"]:
            return name, " ".join(tokens[size:])
    return None


def numbers(text: str, start: int, end: int) -> list[Fragment]:
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
