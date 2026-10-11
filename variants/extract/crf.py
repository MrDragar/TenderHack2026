"""Название и значение без двоеточия различает CRF по словам, обученный на парах « | » листа 1.

Метки слов O/N/V берутся без человека: пары листа 1 разделены « | », название это самый
длинный известный префикс. Признаки слова: само слово, основа, регистр, цифра, единица,
знак, есть ли основа в названиях характеристик листа 2, соседи, слов после запятой.
Пару отдаём, если маргинальная вероятность каждого её слова ≥ 1/2, остальное как бейзлайн.
Учимся на каждом двадцатом товаре вне замера (14.8 тыс., 177 тыс. пар), половину строчными.

Вывод: цена 0.651 → 0.011, верно 12117 → 34342, отказ 22538 → 263, неверно 12 → 62.
Обучение 6 мин и 0.8 Гб, ответ около 10 мс. Без строчных цена 0.010 и неверно 46, но живая
фраза «стол письменный, цвет белый, высота 75 см» склеивала «белый, высота 75 см» в одно
значение. Неверные это запятая внутри значения («нейлон,термопластичный каучук») и шум
слабой разметки; строчное «черный, нагрузка до 120 кг» в живой фразе всё ещё одно значение.
"""

import json
import re
import zlib
from functools import cache
from pathlib import Path

import pycrfsuite
from openpyxl import load_workbook

from contracts.pipeline import Fragment
from pipeline.extract import NUMBER, fragment, number_of, unit_code
from pipeline.extract import extract as baseline
from pipeline.run import portal
from pipeline.text import stem
from pipeline.values import UNIT_ALIASES
from scripts.products import RAW_DIR, SAMPLE, SHEETS, section_names, split_pairs

TRAIN_PATH = Path("data/processed/extract_crf_train.json")
MODEL_PATH = Path("data/processed/extract_crf_lower.model")
# Каждый двадцатый товар вне замера: около десяти тысяч, больше память не вмещает.
EVERY = 20
# Неверная пара стоит вдвое дороже пропущенной: отвечаем, если уверены хотя бы наполовину.
MIN_PROBA = 0.5
TOKEN = re.compile(r"\d+(?:[.,]\d+)*|\w+|[^\w\s]")


def extract(text: str) -> list[Fragment]:
    tokens = [(m.start(), m.end()) for m in TOKEN.finditer(text)]
    if not tokens:
        return baseline(text)
    tagger = trained()
    tagger.set(features(text, tokens))
    labels = tagger.tag()
    found = []
    for name, value in spans(labels):
        sure = min(tagger.marginal(labels[i], i) for i in range(name[0], value[1]))
        if sure >= MIN_PROBA:
            found.append(pair(text, tokens, name, value))
    taken = [(f["start"], f["end"]) for f in found]
    for item in baseline(text):
        if not any(s <= item["start"] < e for s, e in taken):
            found.append(item)
    found.sort(key=lambda f: f["start"])
    return found


def spans(labels: list[str]) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """Пара это подряд идущие N, за ними подряд идущие V."""
    found = []
    i = 0
    while i < len(labels):
        if labels[i] != "N":
            i += 1
            continue
        start = i
        while i < len(labels) and labels[i] == "N":
            i += 1
        middle = i
        while i < len(labels) and labels[i] == "V":
            i += 1
        if i > middle:
            found.append(((start, middle), (middle, i)))
    return found


def pair(
    text: str, tokens: list[tuple[int, int]], name: tuple[int, int], value: tuple[int, int]
) -> Fragment:
    start, end = tokens[name[0]][0], tokens[value[1] - 1][1]
    item = fragment("pair", text[start : tokens[name[1] - 1][1]], text, start, end)
    item["value"] = text[tokens[value[0]][0] : end]
    number = NUMBER.search(item["value"])
    if number:
        item["number"], item["unit"] = number_of(number), unit_code(number)
    return item


def features(text: str, tokens: list[tuple[int, int]]) -> list[dict]:
    known = name_stems()
    words = [text[s:e] for s, e in tokens]
    found = []
    since_comma = 0
    for i, word in enumerate(words):
        since_comma = 0 if i == 0 or words[i - 1] in ",;" else since_comma + 1
        item = token(word, known, "")
        item.update(token(words[i - 1], known, "-1:") if i else {"BOS": 1})
        item.update(token(words[i + 1], known, "+1:") if i + 1 < len(words) else {"EOS": 1})
        if i > 1:
            item["-2:low"] = words[i - 2].lower()
        item["since_comma"] = str(min(since_comma, 4))
        item["space_before"] = i > 0 and tokens[i - 1][1] < tokens[i][0]
        found.append(item)
    return found


def token(word: str, known: frozenset[str], at: str) -> dict:
    low = word.lower()
    return {
        at + "low": low,
        at + "stem": stem(word),
        at + "suffix": low[-3:],
        at + "title": word[:1].isupper() and word[1:2].islower(),
        at + "upper": word.isupper() and len(word) > 1,
        at + "digit": word[:1].isdigit(),
        at + "unit": low in UNIT_ALIASES,
        at + "punct": not word[:1].isalnum(),
        at + "name": stem(word) in known,
    }


@cache
def name_stems() -> frozenset[str]:
    return frozenset(
        stem(word)
        for chars in portal().values()
        for char in chars
        for word in re.findall(r"\w+", char["name"])
    )


@cache
def trained() -> pycrfsuite.Tagger:
    if not MODEL_PATH.exists():
        train()
    tagger = pycrfsuite.Tagger()
    tagger.open(str(MODEL_PATH))
    return tagger


def train() -> None:
    """Признаки по одному товару сразу в crfsuite: в памяти Python их не держим."""
    trainer = pycrfsuite.Trainer(verbose=False)
    for i, (sheet_text, pairs) in enumerate(examples()):
        text = lowered(sheet_text) if i % 2 else sheet_text
        tokens = [(m.start(), m.end()) for m in TOKEN.finditer(text)]
        trainer.append(features(text, tokens), labeled(tokens, pairs))
    trainer.set_params({"c1": 0.1, "c2": 0.01, "max_iterations": 100})
    trainer.train(str(MODEL_PATH))


def lowered(text: str) -> str:
    """Человек пишет «цвет белый» строчными: заглавная буква не должна быть единственной
    приметой начала названия. Длина та же, иначе разметка разъедется."""
    low = text.lower()
    return low if len(low) == len(text) else text


def labeled(tokens: list[tuple[int, int]], pairs: list[tuple[int, int, int]]) -> list[str]:
    labels = []
    for start, _ in tokens:
        label = "O"
        for name_start, value_start, end in pairs:
            if name_start <= start < value_start:
                label = "N"
            elif value_start <= start < end:
                label = "V"
        labels.append(label)
    return labels


def examples() -> list[tuple[str, list[tuple[int, int, int]]]]:
    """Склеено как у судьи: название товара, затем «Название значение» через запятую."""
    if not TRAIN_PATH.exists():
        TRAIN_PATH.write_text(json.dumps(read_sheets(), ensure_ascii=False))
    return json.loads(TRAIN_PATH.read_text())


def read_sheets() -> list[tuple[str, list[tuple[int, int, int]]]]:
    """Товары, которые не попали в выборку замера: иначе учимся на том, по чему меряем."""
    rows = []
    for sheet_name in SHEETS:
        book = load_workbook(RAW_DIR / sheet_name, read_only=True)
        goods, chars = book.worksheets
        sections = section_names(chars.iter_rows(min_row=2, values_only=True), portal())
        names = set().union(*sections.values())
        for row in goods.iter_rows(min_row=2, values_only=True):
            key = zlib.crc32(str(row[0]).encode())
            if not row[0] or not row[1] or not key % SAMPLE or key // SAMPLE % EVERY:
                continue
            text = " ".join(str(row[1]).split())
            pairs = []
            for name, value in split_pairs(str(row[5] or ""), names):
                text += ", "
                pairs.append(
                    (len(text), len(text) + len(name) + 1, len(text) + len(name) + 1 + len(value))
                )
                text += f"{name} {value}"
            if pairs:
                rows.append((text, pairs))
        book.close()
    return rows
