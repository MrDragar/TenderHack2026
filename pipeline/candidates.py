"""Шаблон это argmax P(шаблон | текст): дешёвый отбор двадцати по n-граммам, затем условный логит
на признаках пары, обученный на запросах из самого КТРУ, и отказ по Чоу при P < 1/2.

Признаки: похожесть на название и на весь шаблон, главное слово названия, покрытие слов
названия, совпавшие значения и названия характеристик с весом по редкости, доля слов текста,
которую объясняет шаблон, числа текста внутри числовых значений. Раскладку переводим целым
словом вместе с «,.;[]»: «l;bycs» это «джинсы».
"""

import re
from functools import cache
from random import Random

import numpy as np
from scipy.optimize import minimize, minimize_scalar
from scipy.special import log_softmax, softmax
from sklearn.feature_extraction.text import TfidfVectorizer

from contracts.pipeline import Candidate
from contracts.ref import Position
from pipeline.data import portal
from pipeline.text import LATIN_KEYS, LAYOUT, STOP, TWINS, stem

TOP = 20
MIN_PROBA = 0.5
QUERIES_PER_CODE = 3
KEYBOARD = set(LATIN_KEYS + LATIN_KEYS.upper())
LATIN = re.compile(r"[a-z]", re.IGNORECASE)
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
FEATURES = (
    "name_cos",
    "doc_cos",
    "head",
    "name_cover",
    "value_max",
    "value_sum",
    "char_sum",
    "explained",
    "numbers",
)

MODELS: dict[tuple[str, ...], tuple[dict, np.ndarray, float]] = {}


def find(text: str, ref: list[Position]) -> list[Candidate]:
    index, weights, temperature = model(ref)
    query = tokens(text)
    if not query:
        return []
    top, feats = shortlist(" ".join(query), query, numbers(text), index)
    proba = softmax(feats @ weights / temperature)
    order = np.argsort(-proba)
    if proba[order[0]] < MIN_PROBA:
        return []
    found: list[Candidate] = []
    for rank in order:
        if rank != order[0] and proba[rank] < 1 - MIN_PROBA:
            break
        template = index["templates"][top[rank]]
        finals = index["finals"][template]
        for code, name in finals:
            found.append({"code": code, "name": name, "score": float(proba[rank]) / len(finals)})
    return found


def tokens(text: str) -> list[str]:
    """Слово целиком в латинской раскладке, включая «;,.[]'» внутри: «l;bycs» это «джинсы»."""
    found = []
    for chunk in text.split():
        if LATIN.search(chunk) and set(chunk) <= KEYBOARD:
            chunk = chunk.rstrip(",;.") or chunk
            chunk = chunk.translate(LAYOUT)
        else:
            chunk = chunk.translate(TWINS)
        for word in re.findall(r"[а-яёa-z]+", chunk.lower().replace("ё", "е")):
            if len(word) >= 3 and word not in STOP:
                found.append(word)
    return found


@cache
def bigrams(word: str) -> frozenset[str]:
    padded = f"^{word}$"
    return frozenset(padded[i : i + 2] for i in range(len(padded) - 1))


@cache
def similar(left: str, right: str) -> float:
    """Совпала основа: то же слово. Иначе доля общих биграмм выше половины, растянутая в 0..1."""
    if stem(left) == stem(right):
        return 1.0
    a, b = bigrams(left), bigrams(right)
    dice = 2 * len(a & b) / (len(a) + len(b))
    return max(0.0, 2 * dice - 1)


def best(word: str, query: list[str]) -> float:
    return max(similar(word, other) for other in query)


def phrase(phrase_words: tuple[str, ...], query: list[str]) -> float:
    return min(best(word, query) for word in phrase_words) if phrase_words else 0.0


def model(ref: list[Position]) -> tuple[dict, np.ndarray, float]:
    key = tuple(p["code"] for p in ref)
    if key not in MODELS:
        index = build(ref)
        MODELS[key] = (index, *trained(ref, index))
    return MODELS[key]


def build(ref: list[Position]) -> dict:
    finals: dict[str, list[tuple[str, str]]] = {}
    names: dict[str, str] = {}
    values: dict[str, set[tuple[str, ...]]] = {}
    chars: dict[str, set[tuple[str, ...]]] = {}
    numeric: dict[str, list] = {}
    for p in ref:
        if p["position_type"] != "final":
            continue
        template = p["parent_code"] or p["code"]
        finals.setdefault(template, []).append((p["code"], p["name"]))
        names.setdefault(template, p["name"])
        for char in p["characteristics"]:
            chars.setdefault(template, set()).add(tuple(tokens(char["name"])))
            if char["type"] == "number":
                numeric.setdefault(template, []).extend(char["values"])
            for value in char["values"]:
                if isinstance(value, str):
                    values.setdefault(template, set()).add(tuple(tokens(value)))
    templates = sorted(finals)
    # Значение или название характеристики, общее для многих шаблонов, мало говорит о шаблоне.
    value_idf = idf([values.get(t, set()) for t in templates])
    char_idf = idf([chars.get(t, set()) for t in templates])
    docs = [
        " ".join(
            [
                names[t],
                *(" ".join(v) for v in values.get(t, ())),
                *(" ".join(c) for c in chars.get(t, ())),
            ]
        )
        for t in templates
    ]
    name_texts = [" ".join(tokens(names[t])) for t in templates]
    vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True)
    vectorizer.fit(name_texts + docs)
    return {
        "templates": templates,
        "finals": finals,
        "name_words": [tuple(tokens(names[t])) for t in templates],
        "values": [sorted(values.get(t, set()) - {()}) for t in templates],
        "chars": [sorted(chars.get(t, set()) - {()}) for t in templates],
        "numbers": [numeric.get(t, []) for t in templates],
        "value_idf": value_idf,
        "char_idf": char_idf,
        "vectorizer": vectorizer,
        "name_matrix": vectorizer.transform(name_texts),
        "doc_matrix": vectorizer.transform(docs),
    }


def idf(sets: list[set[tuple[str, ...]]]) -> dict[tuple[str, ...], float]:
    counts: dict[tuple[str, ...], int] = {}
    for found in sets:
        for item in found:
            counts[item] = counts.get(item, 0) + 1
    return {item: float(np.log(len(sets) / count)) for item, count in counts.items()}


def shortlist(
    text: str, query: list[str], said_numbers: list[float], index: dict
) -> tuple[np.ndarray, np.ndarray]:
    vector = index["vectorizer"].transform([text])
    name_cos = (index["name_matrix"] @ vector.T).toarray().ravel()
    doc_cos = (index["doc_matrix"] @ vector.T).toarray().ravel()
    top = np.argsort(-(name_cos + doc_cos))[:TOP]
    fitting = number_fits(said_numbers, index["numbers"])
    feats = []
    for i in top:
        fit = number_score(fitting, i, len(index["numbers"]))
        feats.append([*pair(query, i, name_cos[i], doc_cos[i], index), fit])
    return top, np.array(feats)


def pair(query: list[str], i: int, name_cos: float, doc_cos: float, index: dict) -> list[float]:
    name_words = index["name_words"][i]
    vocab = set(name_words)
    value_scores = []
    for value in index["values"][i]:
        value_scores.append(index["value_idf"][value] * phrase(value, query))
        vocab.update(value)
    char_sum = 0.0
    for char in index["chars"][i]:
        char_sum += index["char_idf"][char] * phrase(char, query)
        vocab.update(char)
    # Первое слово названия КТРУ это сам предмет: «Стол производственный» про стол.
    head = best(name_words[0], query) if name_words else 0.0
    cover = float(np.mean([best(w, query) for w in name_words])) if name_words else 0.0
    explained = float(np.mean([max((similar(w, v) for v in vocab), default=0.0) for w in query]))
    return [
        name_cos,
        doc_cos,
        head,
        cover,
        max(value_scores, default=0.0),
        float(np.log1p(sum(value_scores))),
        float(np.log1p(char_sum)),
        explained,
    ]


def trained(ref: list[Position], index: dict) -> tuple[np.ndarray, float]:
    """Условный логит по двадцати отобранным; температура на десятой части шаблонов."""
    rng = Random(0)
    finals = [p for p in ref if p["position_type"] == "final"]
    noise = [char for chars in portal().values() for char in chars]
    position_of = {t: i for i, t in enumerate(index["templates"])}
    fit, held = [], []
    for p in finals:
        template = p["parent_code"] or p["code"]
        for _ in range(QUERIES_PER_CODE):
            text = pseudo_query(p, finals, noise, rng)
            query = tokens(text)
            if not query:
                continue
            top, feats = shortlist(" ".join(query), query, numbers(text), index)
            gold = np.flatnonzero(top == position_of[template])
            if len(gold):
                (held if rng.random() < 0.1 else fit).append((feats, int(gold[0])))

    def loss(weights: np.ndarray, rows: list) -> float:
        return (
            -sum(log_softmax(f @ weights)[g] for f, g in rows) / len(rows)
            + 1e-3 * weights @ weights
        )

    weights = minimize(loss, np.zeros(len(FEATURES)), args=(fit,), method="L-BFGS-B").x

    def held_loss(temperature: float) -> float:
        return -sum(log_softmax(f @ weights / temperature)[g] for f, g in held) / len(held)

    return weights, minimize_scalar(held_loss, bounds=(0.1, 20), method="bounded").x


def pseudo_query(position: Position, finals: list[Position], noise: list, rng: Random) -> str:
    """Главное слово названия и часть уточнений, несколько значений позиции, бывает значение
    чужой позиции и характеристика портала: у живых товаров рядом страна, гарантия, цвет.
    Уточнения опускают, предмет нет."""
    head, *rest = position["name"].split()
    kept = [word for word in rest if rng.random() < 0.5]
    parts = [" ".join([head, *kept])]
    chars = position["characteristics"]
    for char in rng.sample(chars, min(len(chars), rng.randint(1, 3))):
        parts.append(said(char, rng))
    if rng.random() < 0.5:
        other = rng.choice(finals)["characteristics"]
        if other:
            parts.append(said(rng.choice(other), rng))
    if rng.random() < 0.5:
        parts.append(portal_said(rng.choice(noise), rng))
    rng.shuffle(parts)
    return ", ".join(parts)


def said(char: dict, rng: Random) -> str:
    texts = [shown(v) for v in char["values"]]
    value = rng.choice(texts) if texts else ""
    if not texts or rng.random() < 0.5:
        return f"{char['name']} {value}".strip()
    return value


def portal_said(char: dict, rng: Random) -> str:
    value = rng.choice(char["values"]) if char["values"] else ""
    if len(value.split()) > 3:
        value = ""
    return f"{char['name']} {value}".strip()


def shown(value) -> str:
    if not isinstance(value, dict):
        return str(value)
    ends = [value[side] for side in ("min", "max") if side in value]
    return f"{sum(ends) / len(ends):g}"


def numbers(text: str) -> list[float]:
    return [float(n.replace(",", ".")) for n in NUMBER.findall(text)]


def number_fits(said_numbers: list[float], allowed: list[list]) -> list[set[int]]:
    """Для каждого числа текста шаблоны, в чьё числовое значение оно попало."""
    found = []
    for number in said_numbers:
        fitting = set()
        for i, values in enumerate(allowed):
            if any(fits(number, value) for value in values):
                fitting.add(i)
        found.append(fitting)
    return found


def number_score(fitting: list[set[int]], i: int, total: int) -> float:
    """Как idf: число, подходящее почти любому шаблону, ничего не говорит, а 0,35 мм Pb
    в двух шаблонах из двухсот говорит много."""
    scores = [np.log(total / len(f)) for f in fitting if i in f]
    return float(sum(scores) / len(fitting)) if fitting else 0.0


def fits(number: float, value) -> bool:
    if not isinstance(value, dict):
        return isinstance(value, int | float) and number == value
    low = value.get("min", -np.inf)
    high = value.get("max", np.inf)
    return low <= number <= high
