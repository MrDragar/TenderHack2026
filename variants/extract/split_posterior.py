"""Пару отдаём, только если её вероятность по всем разборам строки больше 1/2, иначе отказ.

Решётка и выигрыши те же, что в split_probability, но вместо одного разбора Витерби
вперёд-назад даёт маргинал каждой пары (начало, разрез, конец) при весе разбора exp(сумма / T).
T = 1.5 это минимум логарифмической потери верной пары на синтетике из листа 2 (calibrate,
запуск python -m variants.extract.split_posterior), цена 0/1/2 там плоская от 0.25 до 2.
Предсказание: часть неверных недобранных списков «торцевые, свечные» уходит в отказ, потому что
масса делится между «торцевые» и «торцевые, свечные»; верные почти не теряются.

Вывод: не сработало. Dev при T = 1.5: 26748 верно / 785 отказ / 252 неверно, цена 0.046 против
0.044 у split_probability; при T = 1 (теоретическом) 26816 / 706 / 263, тоже 0.044. В отказ
ушли 31 неверных и 106 верных («Пол Женские», «Модель Поло»), при T = 1 это 19 и 38. Недобранный
список остаётся неверным: модель уверенно считает «свечные» фразой о товаре, маргинал
«торцевые» близок к 1. Это смещение модели, а не неуверенность, и калибровка его не лечит.
Прогон судьи 2.3 мин, калибровка на 3000 синтетических текстах 45 с.
"""

import math
import random
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
TOKEN = re.compile(r"\d+(?:[.,]\d+)?|[^\W\d_]+")
SPAN = 6
T = 1.5


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
    """Вперёд-назад по той же решётке фраз, что Витерби в split_probability: вес разбора
    exp(сумма выигрышей / T), фраза без пары весит exp(0). Пары с маргиналом больше 1/2 не
    могут пересекаться: в одном разборе их нет вместе, а сумма маргиналов не больше 1. Поэтому
    набор по правилу Чоу сам является разбором, и это минимум ожидаемой цены 0/1/2 по парам."""
    found = []
    for (start, cut, end), mass in posterior(text, clauses, T).items():
        if mass > math.log(0.5):
            found.append(split(text, start, cut, end))
    return found


def posterior(
    text: str, clauses: list[tuple[int, int]], temperature: float
) -> dict[tuple[int, int, int], float]:
    """Логарифм апостериорной вероятности каждой пары (начало, разрез, конец) по всем разборам."""
    n = len(clauses)
    spans = []
    for j in range(n):
        for i in range(max(0, j + 1 - SPAN), j + 1):
            for cut, gain in cuts(text, clauses[i][0], clauses[j][1]):
                spans.append((i, j, cut, (gain + (j - i) * model()["comma"]) / temperature))
    forward = [0.0] + [-math.inf] * n
    by_end: dict[int, list] = {}
    for span in spans:
        by_end.setdefault(span[1], []).append(span)
    for j in range(n):
        terms = [forward[j]] + [forward[i] + w for i, _, _, w in by_end.get(j, [])]
        forward[j + 1] = logsumexp(terms)
    backward = [-math.inf] * n + [0.0]
    by_start: dict[int, list] = {}
    for span in spans:
        by_start.setdefault(span[0], []).append(span)
    for i in reversed(range(n)):
        terms = [backward[i + 1]] + [w + backward[j + 1] for _, j, _, w in by_start.get(i, [])]
        backward[i] = logsumexp(terms)
    found = {}
    for i, j, cut, w in spans:
        found[clauses[i][0], cut, clauses[j][1]] = forward[i] + w + backward[j + 1] - forward[n]
    return found


def logsumexp(terms: list[float]) -> float:
    top = max(terms)
    if top == -math.inf:
        return top
    return top + math.log(sum(math.exp(t - top) for t in terms))


@cache
def cuts(text: str, start: int, end: int) -> list[tuple[int, float]]:
    found = []
    for match in re.finditer(r"(?<=\s)\S", text[start:end]):
        cut = start + match.start()
        name = text[start:cut].strip()
        found.append((cut, name_gain(name) + value_gain(text[cut:end], name)))
    return found


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
    stats = model()
    own = stats["values"].get(name.casefold(), Counter())
    size = own.total() + len(own)
    gain = 0.0
    for token in tokenize(value):
        likely = prob(token, "value_words")
        if size:
            likely = (own[token] + len(own) * likely) / size
        gain += math.log(likely) - math.log(prob(token, "background"))
    return gain


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


def synthetic(count: int = 3000) -> list[tuple[str, list[tuple[int, int, int]]]]:
    """Тексты из листа 2 как у судьи: название раздела, затем «Название значение» через
    запятую. Значений у пары столько, сколько даёт доля запятых на значение листа 2."""
    rng = random.Random(0)
    sections = [(name, chars) for name, chars in portal().items() if chars]
    more = math.exp(model()["comma"])
    texts = []
    for _ in range(count):
        section, chars = rng.choice(sections)
        text = section
        pairs = []
        for char in rng.sample(chars, min(len(chars), rng.randint(1, 6))):
            if char["values"]:
                listed = [rng.choice(char["values"])]
                while rng.random() < more:
                    listed.append(rng.choice(char["values"]))
                value = ", ".join(dict.fromkeys(listed))
            else:
                value = f"{rng.randint(1, 200)} {char['unit'] or ''}".strip()
            text += ", "
            pairs.append((len(text), len(text) + len(char["name"]) + 1))
            text += f"{char['name']} {value}"
            pairs[-1] = (*pairs[-1], len(text))
        texts.append((text, pairs))
    return texts


def calibrate() -> None:
    """Средний -log P(верной пары) на синтетике при разных T: эталон в подбор не попадает,
    а логарифмическая потеря, в отличие от цены, честно меряет саму вероятность."""
    texts = synthetic()
    for temperature in (0.25, 0.5, 1.0, 1.5, 2.0, 3.0):
        loss = []
        for text, pairs in texts:
            clauses = [stripped(text, c.start(), c.end()) for c in CLAUSE.finditer(text)]
            masses = posterior(text, clauses, temperature)
            loss.extend(-masses.get(gold, -30.0) for gold in pairs)
        print(temperature, round(sum(loss) / len(loss), 4))


if __name__ == "__main__":
    calibrate()
