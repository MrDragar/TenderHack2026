"""Фон, словари значений и цена запятой, переоценённые EM-ом на текстах листа 1, уберут
недобранные списки и ложные пары split_probability.

Предсказание: меньше неверных (списки «торцевые, свечные» дособираются), отказы не вырастут.
"""

import json
import math
import re
import zlib
from collections import Counter
from functools import cache
from pathlib import Path
from typing import Literal

from openpyxl import load_workbook

from contracts.pipeline import Fragment
from pipeline.run import portal, reference
from pipeline.text import PAIR, words
from pipeline.values import UNIT_ALIASES, show
from scripts.products import RAW_DIR, SAMPLE, SHEETS

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


EM_PATH = Path("data/processed/extract_em.json")
# Каждый восьмидесятый товар вне замера: около 3700 текстов, итерация около полутора минут.
EVERY = 160
ROUNDS = 2
# Счёт EM меньше половины слова это шум мягких апостериорных, он раздувал бы число разных
# слов в Виттене-Белле.
MIN_COUNT = 0.5
CURRENT: dict = {}


def extract(text: str) -> list[Fragment]:
    found = []
    for match in PAIR.finditer(text):
        found.append(pair(text, match))
    for run in runs(text):
        for start, end in run:
            found.extend(numbers(text, start, end))
            if words(text[start:end]):
                found.append(fragment("words", None, text, start, end))
        found.extend(segment(text, run))
    found.sort(key=lambda f: f["start"])
    return found


def runs(text: str) -> list[list[tuple[int, int]]]:
    """Подряд идущие фразы без двоеточия: пара «Название: значение» рвёт цепочку."""
    pairs = [(m.start("name"), m.end("value")) for m in PAIR.finditer(text)]
    found: list[list[tuple[int, int]]] = [[]]
    for clause in CLAUSE.finditer(text):
        start, end = stripped(text, clause.start(), clause.end())
        if start == end:
            continue
        if any(s <= start < e for s, e in pairs):
            found.append([])
            continue
        found[-1].append((start, end))
    return [run for run in found if run]


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
    for cut, gain in all_cuts(text, start, end):
        if gain > best_gain:
            best, best_gain = cut, gain
    return best, best_gain


@cache
def all_cuts(text: str, start: int, end: int) -> list[tuple[int, float]]:
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


def model() -> dict:
    if not CURRENT:
        if not EM_PATH.exists():
            train()
        use(finished(json.loads(EM_PATH.read_text(encoding="utf-8"))))
    return CURRENT


def use(stats: dict) -> None:
    CURRENT.clear()
    CURRENT.update(stats)
    for function in (best_cut, all_cuts, name_gain, value_gain):
        function.cache_clear()


def train() -> None:
    """MAP-EM: справочные частоты остаются псевдосчётами, к ним прибавляются ожидаемые счёты
    по текстам листа 1. Название пары считается, только если оно есть в справочнике: голый EM
    назначал бы названиями что угодно (Grenager, Klein, Manning 2005)."""
    prior = reference_counts()
    texts = sheet_texts()
    known = set(prior["names"])
    counts = prior
    for _ in range(ROUNDS):
        use(finished(counts))
        counts = mixed(prior, expected(texts, known))
    EM_PATH.parent.mkdir(parents=True, exist_ok=True)
    EM_PATH.write_text(json.dumps(counts, ensure_ascii=False), encoding="utf-8")


def expected(texts: list[str], known: set[str]) -> dict:
    """Прямой-обратный ход по той же решётке, что и Витерби, но по всем разрезам. Фраза,
    которая начинается с названия из справочника, это начало пары, а не фон и не хвост чужого
    значения: без этого EM учил фон словам «тип», а значения названиям следующих пар."""
    names: Counter[str] = Counter()
    values: dict[str, Counter[str]] = {}
    background: Counter[str] = Counter()
    commas = 0.0
    pairs = 0.0
    comma = model()["comma"]
    for text in texts:
        for run in runs(text):
            named = [starts_with_name(text[start:end], known) for start, end in run]
            arcs = []
            for j in range(len(run)):
                for i in range(max(0, j + 1 - SPAN), j + 1):
                    if any(named[i + 1 : j + 1]):
                        continue
                    for cut, gain in all_cuts(text, run[i][0], run[j][1]):
                        arcs.append((i, j, cut, gain + (j - i) * comma))
            n = len(run)
            ends: list[list[tuple[int, float]]] = [[] for _ in range(n)]
            starts: list[list[tuple[int, float]]] = [[] for _ in range(n)]
            for i, j, _, gain in arcs:
                ends[j].append((i, gain))
                starts[i].append((j, gain))
            alpha = [0.0] * (n + 1)
            for k in range(n):
                alpha[k + 1] = -math.inf if named[k] else alpha[k]
                for i, gain in ends[k]:
                    alpha[k + 1] = logadd(alpha[k + 1], alpha[i] + gain)
            beta = [0.0] * (n + 1)
            for k in range(n - 1, -1, -1):
                beta[k] = -math.inf if named[k] else beta[k + 1]
                for j, gain in starts[k]:
                    beta[k] = logadd(beta[k], gain + beta[j + 1])
            total = alpha[n]
            for k, (start, end) in enumerate(run):
                if named[k]:
                    continue
                share = math.exp(alpha[k] + beta[k + 1] - total)
                for token in tokenize(text[start:end]):
                    background[token] += share
            for i, j, cut, gain in arcs:
                share = math.exp(alpha[i] + gain + beta[j + 1] - total)
                name = text[run[i][0] : cut].strip().casefold()
                if share < 0.01 or name not in known:
                    continue
                names[name] += share
                own = values.setdefault(name, Counter())
                for token in tokenize(text[cut : run[j][1]]):
                    own[token] += share
                commas += share * (j - i)
                pairs += share
    return {
        "names": pruned(names),
        "values": {name: pruned(own) for name, own in values.items()},
        "background": pruned(background),
        "commas": commas,
        "pairs": pairs,
    }


def starts_with_name(clause: str, known: set[str]) -> bool:
    parts = clause.casefold().split()
    return any(" ".join(parts[:size]) in known for size in range(1, len(parts)))


def logadd(a: float, b: float) -> float:
    if a < b:
        a, b = b, a
    if b == -math.inf:
        return a
    return a + math.log1p(math.exp(b - a))


def pruned(counts: Counter[str]) -> dict[str, float]:
    return {token: count for token, count in counts.items() if count >= MIN_COUNT}


def mixed(prior: dict, em: dict) -> dict:
    values = {name: dict(own) for name, own in prior["values"].items()}
    for name, own in em["values"].items():
        values[name] = dict(Counter(values.get(name, {})) + Counter(own))
    return {
        "names": dict(Counter(prior["names"]) + Counter(em["names"])),
        "values": values,
        "background": dict(Counter(prior["background"]) + Counter(em["background"])),
        "commas": prior["commas"] + em["commas"],
        "pairs": prior["pairs"] + em["pairs"],
        "new_name": prior["new_name"],
    }


def sheet_texts() -> list[str]:
    """Тексты товаров вне выборки замера, собранные как у судьи, но без разбора на пары: метки
    пар не нужны, «|» в листе 1 просто становится запятой."""
    texts = []
    for sheet_name in SHEETS:
        book = load_workbook(RAW_DIR / sheet_name, read_only=True)
        for row in book.worksheets[0].iter_rows(min_row=2, values_only=True):
            key = zlib.crc32(str(row[0]).encode())
            if not row[0] or not row[1] or not key % SAMPLE or key // SAMPLE % EVERY:
                continue
            parts = [" ".join(str(row[1]).split())]
            parts += [" ".join(part.split()) for part in str(row[5] or "").split(" | ")]
            texts.append(", ".join(part for part in parts if part))
        book.close()
    return texts


def reference_counts() -> dict:
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
    singles = sum(1 for count in names.values() if count == 1)
    return {
        "names": dict(names),
        "values": {name: dict(own) for name, own in values.items()},
        "background": dict(background),
        "commas": commas,
        "pairs": portal_values,
        "new_name": singles / names.total(),
    }


def finished(counts: dict) -> dict:
    names = Counter(counts["names"])
    values = {name: Counter(own) for name, own in counts["values"].items()}
    background = Counter(counts["background"])
    name_words: Counter[str] = Counter()
    for name, count in names.items():
        for token in tokenize(name):
            name_words[token] += count
    value_words: Counter[str] = Counter()
    for own in values.values():
        value_words.update(own)
    return {
        "names": names,
        "names_total": names.total(),
        "new_name": counts["new_name"],
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
        # Цена запятой внутри пары: сколько запятых приходится на одно значение.
        "comma": math.log(counts["commas"] / counts["pairs"]),
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
