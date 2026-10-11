"""Однородные члены списка грамматически похожи: значение-список «торцевые, свечные» узнаём
по тому, как классы (pymorphy3) начал соседних элементов списков листа 2 идут друг за другом.

Основа split_probability. Начало каждого следующего элемента значения получает
log P(класс | класс соседа слева) / P(класс) по спискам листа 2 (Shimbo, Hara 2007).
Окно Витерби 16 фраз вместо 6: списки длиннее шести элементов обрезались.
Первая версия ещё оценивала незнакомое для названия слово через класс:
P(класс | новые слова названия) * P(слово | класс), и фон тоже по классам. Вывод по ней: dev
26889 верно / 656 отказ / 240 неверно (0.041) против 26856 / 649 / 280 (0.044). Недобранных
списков 252 → 205, но почти всё это окно 16: на четверти dev split_probability с окном 16
даёт 0.0448, с классами 0.0464, а с классами и окном 6 хуже основы (0.0503 против 0.0482).
Класс делает продолжение через запятую дешёвым: «значение забирает следующую пару» 22 → 29,
и ещё 79 пар из верных ушли в отказ, их проглотило значение соседа («пластик, Ручка
складная»). Предсказание провалилось. Сочинительная связь по одному первому слову слабый
сигнал: следующая пара «Ручка складная» начинается так же, как элемент списка, существительным
в именительном. Вторая версия: только сочинение и окно, без классов в словаре значений.
Предсказание: на четверти dev 0.0441 против 0.0448 у основы с окном 16, то есть почти весь
выигрыш от окна.
"""

import math
import re
from collections import Counter
from functools import cache
from typing import Literal

from pymorphy3 import MorphAnalyzer

from contracts.pipeline import Fragment
from pipeline.run import portal, reference
from pipeline.text import PAIR, words
from pipeline.values import UNIT_ALIASES, show

MORPH = MorphAnalyzer()

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
TOKEN = re.compile(r"\d+(?:[.,]\d+)?|[^\W\d_]+")
SPAN = 16


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


def value_gain(value: str, name: str) -> float:
    """Значение-список «торцевые, свечные»: каждый элемент оценивается словами этого названия,
    а начало следующего элемента ещё и по тому, как классы соседних элементов списка
    согласуются в листе 2 (Shimbo, Hara 2007: однородные члены похожи друг на друга)."""
    gain = 0.0
    previous = None
    for piece in CLAUSE.findall(value):
        tokens = tokenize(piece)
        if not tokens:
            continue
        gain += piece_gain(piece, name)
        first = klass(tokens[0])
        if previous is not None:
            gain += coordination(previous, first)
        previous = first
    return gain


@cache
def piece_gain(piece: str, name: str) -> float:
    """Слова значения из словаря этого названия, незнакомое слово из общего словаря значений с
    долей, как часто у названия встречались новые слова: у «Бренда» почти всегда."""
    stats = model()
    own = stats["values"].get(name.casefold(), Counter())
    size = own.total() + len(own)
    gain = 0.0
    for token in tokenize(piece):
        likely = prob(token, "value_words")
        if size:
            likely = (own[token] + len(own) * likely) / size
        gain += math.log(likely) - math.log(prob(token, "background"))
    return gain


@cache
def coordination(previous: str, cls: str) -> float:
    """log P(класс | класс соседа слева) / P(класс) по элементам списков листа 2."""
    stats = model()
    after = stats["neighbours"].get(previous, Counter())
    alone = (stats["elements"][cls] + 1) / (stats["elements"].total() + stats["classes"])
    near = (after[cls] + len(after) * alone) / (after.total() + len(after)) if after else alone
    return math.log(near / alone)


@cache
def klass(token: str) -> str:
    """Часть речи, число и падеж без рода: «торцевые» и «свечные» один класс."""
    if token == "<num>":
        return "NUMB"
    tag = MORPH.parse(token)[0].tag
    return " ".join(str(part) for part in (tag.POS or tag, tag.number, tag.case) if part)


def prob(token: str, kind: str) -> float:
    """Виттен-Белл: доля незнакомых слов в словаре тем больше, чем больше в нём разных слов.
    Без этого бренд, которого нет нигде, ронял бы значение сильнее, чем фразу."""
    stats = model()
    counts = stats[kind]
    size = stats["totals"][kind] + len(counts)
    if counts[token]:
        return counts[token] / size
    return len(counts) / size / stats["vocabulary"]


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
    value_words: Counter[str] = Counter()
    for counts in values.values():
        value_words.update(counts)
    neighbours: dict[str, Counter[str]] = {}
    elements: Counter[str] = Counter()
    for chars in portal().values():
        for char in chars:
            for value in char["values"]:
                heads = [tokenize(piece) for piece in CLAUSE.findall(value)]
                heads = [klass(tokens[0]) for tokens in heads if tokens]
                for left, right in zip(heads, heads[1:], strict=False):
                    neighbours.setdefault(left, Counter())[right] += 1
                    elements[right] += 1
    singles = sum(1 for count in names.values() if count == 1)
    return {
        "names": names,
        "names_total": names.total(),
        "new_name": singles / names.total(),
        "values": values,
        "name_words": name_words,
        "value_words": value_words,
        "background": background,
        "vocabulary": len(name_words | value_words | background) + 1,
        "classes": len(elements) + 1,
        "neighbours": neighbours,
        "elements": elements,
        "totals": {
            "name_words": name_words.total(),
            "value_words": value_words.total(),
            "background": background.total(),
        },
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
    item = fragment("pair", match["name"].strip(), text, start, end)
    item["value"] = match["value"].strip()
    number = NUMBER.search(item["value"])
    if number:
        item["number"], item["unit"] = number_of(number), unit_code(number)
    return item


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
