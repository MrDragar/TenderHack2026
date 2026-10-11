"""Шаблон это argmax softmax(BM25F/T) по символьным триграммам полей позиции, отказ при P < 1/2.

Поля: первое слово названия (сам товар, вес 3), остальное название и текстовые значения (1),
названия характеристик (0.5). Раскладку переводим целыми словами вместе с «,.;[]». Шаблон
вбирает вероятности своих конечных позиций. T ≈ 4.4 подобрана на самом КТРУ: запрос это
название или его первое слово, половина значений и одно чужое слово.

Вывод: цена 0.200 → 0.090, верно 268 → 282, отказ 4 → 9, неверно 28 → 9. Без названий
характеристик цена 0.247, триграммы чуть лучше 3–5-грамм (0.097), голос «одно слово запроса
это одна единица» хуже (0.137). Остались: «шорты» уходит в «Шорты детские», потому что сумма
BM25 не штрафует несовпавшие слова названия; «производство» совпадает с «производственный».
"""

import re
from random import Random

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.sparse import csr_matrix, diags
from scipy.special import logsumexp
from sklearn.feature_extraction.text import CountVectorizer

from contracts.pipeline import Candidate
from contracts.ref import Position

LATIN_KEYS = "qwertyuiop[]asdfghjkl;'zxcvbnm,.`"
RUSSIAN_KEYS = "йцукенгшщзхъфывапролджэячсмитьбюе"
LAYOUT = str.maketrans(LATIN_KEYS, RUSSIAN_KEYS)
TWINS = str.maketrans("aceopxyk", "асеорхук")
LATIN = re.compile(r"[a-z]")
CYRILLIC = re.compile(r"[а-яё]")
# Первое слово названия позиции это сам товар, остальные слова только уточняют его,
# а значения и названия характеристик лишь подсказывают.
FIELDS = {"head": 3.0, "name": 1.0, "values": 1.0, "chars": 0.5}
K1 = 1.2
B = 0.75
MIN_PROBA = 0.5

_indexes: dict[tuple[str, ...], dict] = {}


def find(text: str, ref: list[Position]) -> list[Candidate]:
    index = indexed(ref)
    query = normalized(text)
    if not query.strip():
        return []
    scores = bm25(index, [query])[0]
    templates, proba = template_proba(index, scores / index["temperature"])
    best = int(proba.argmax())
    if proba[best] < MIN_PROBA:
        return []
    order = np.argsort(-proba)[:5]
    found = []
    for at in order:
        members = index["members"][templates[at]]
        final = max(members, key=lambda doc: scores[doc])
        position = index["finals"][final]
        found.append(
            {"code": position["code"], "name": position["name"], "score": float(proba[at])}
        )
    return found


def normalized(text: str) -> str:
    """Слово целиком латиницей переводим по клавишам вместе с «,.;[]»: «,h.rb» это «брюки»."""
    lowered = text.lower().replace("ё", "е")
    return re.sub(r"\S+", fixed_word, lowered)


def fixed_word(match: re.Match) -> str:
    word = match[0]
    if not LATIN.search(word):
        return word
    if CYRILLIC.search(word):
        return word.translate(TWINS)
    # Знак в конце скорее разделитель фраз, чем буква: «l;bycs;» это «джинсы;».
    body = word.rstrip(",;.")
    return body.translate(LAYOUT) + word[len(body) :]


def fields(position: Position) -> dict[str, str]:
    values = [
        str(value)
        for char in position["characteristics"]
        if char["type"] == "text"
        for value in char["values"]
    ]
    head, _, rest = position["name"].partition(" ")
    return {
        "head": head,
        "name": rest,
        "values": " ".join(values),
        "chars": " ".join(char["name"] for char in position["characteristics"]),
    }


def indexed(ref: list[Position]) -> dict:
    key = tuple(p["code"] for p in ref)
    if key not in _indexes:
        _indexes[key] = build(ref)
    return _indexes[key]


def build(ref: list[Position]) -> dict:
    finals = [p for p in ref if p["position_type"] == "final"]
    texts = {name: [normalized(fields(p)[name]) for p in finals] for name in FIELDS}
    vectorizer = CountVectorizer(analyzer="char_wb", ngram_range=(3, 3), lowercase=False)
    vectorizer.fit([text for column in texts.values() for text in column])
    weighted = None
    for name, weight in FIELDS.items():
        counts = vectorizer.transform(texts[name]).astype(float)
        lengths = np.asarray(counts.sum(axis=1)).ravel()
        norm = 1 - B + B * lengths / max(lengths.mean(), 1.0)
        part = diags(weight / norm) @ counts
        weighted = part if weighted is None else weighted + part
    weighted = csr_matrix(weighted)
    weighted.data = weighted.data * (K1 + 1) / (weighted.data + K1)
    df = np.bincount(weighted.indices, minlength=weighted.shape[1])
    idf = np.log(1 + (len(finals) - df + 0.5) / (df + 0.5))
    template_codes = [p["parent_code"] or p["code"] for p in finals]
    templates = sorted(set(template_codes))
    members: dict[str, list[int]] = {code: [] for code in templates}
    for doc, code in enumerate(template_codes):
        members[code].append(doc)
    index = {
        "finals": finals,
        "vectorizer": vectorizer,
        "matrix": csr_matrix(weighted @ diags(idf)).T.tocsr(),
        "templates": templates,
        "members": members,
        "of": np.array([templates.index(code) for code in template_codes]),
        "temperature": 1.0,
    }
    index["temperature"] = calibrated(index)
    return index


def bm25(index: dict, queries: list[str]) -> np.ndarray:
    grams = index["vectorizer"].transform(queries)
    grams.data[:] = 1.0
    return np.asarray((grams @ index["matrix"]).todense())


def template_proba(index: dict, logits: np.ndarray) -> tuple[list[str], np.ndarray]:
    """Шаблон вбирает вероятности всех своих конечных позиций."""
    joint = np.empty((*logits.shape[:-1], len(index["templates"])))
    for at, code in enumerate(index["templates"]):
        docs = index["members"][code]
        joint[..., at] = logsumexp(logits[..., docs], axis=-1)
    return index["templates"], np.exp(joint - logsumexp(joint, axis=-1, keepdims=True))


def calibrated(index: dict) -> float:
    """T подбираем на самом КТРУ, а не на эталоне: запрос собираем из своей позиции и чужого слова.

    Запрос это название или только его первое слово, по значению половины характеристик и одно
    слово из названия случайной другой позиции: люди пишут коротко и с посторонними словами.
    """
    rng = Random(0)
    finals = index["finals"]
    queries = [pseudo_query(position, rng.choice(finals), rng) for position in finals]
    scores = bm25(index, [normalized(query) for query in queries])
    truth = index["of"]

    def loss(temperature: float) -> float:
        _, proba = template_proba(index, scores / temperature)
        return -np.log(proba[np.arange(len(truth)), truth] + 1e-12).mean()

    return minimize_scalar(loss, bounds=(0.1, 100), method="bounded").x


def pseudo_query(position: Position, other: Position, rng: Random) -> str:
    texts = []
    for char in position["characteristics"]:
        if char["type"] == "text" and char["values"]:
            texts.append(str(rng.choice(char["values"])))
    name = position["name"] if rng.random() < 0.5 else position["name"].split()[0]
    stranger = rng.choice(other["name"].split())
    return " ".join([name, *rng.sample(texts, len(texts) // 2), stranger])
