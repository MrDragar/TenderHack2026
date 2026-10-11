"""Шаблон это argmax softmax(смысловое + буквенное сходство текста с позицией): гибридный поиск.

Сходство текста с названием конечной позиции: среднее косинуса e5-small (лучший кусок текста
между запятыми) и косинуса TF-IDF по буквенным n-граммам, у шаблона максимум по его позициям.
Раскладку чинит russian() до обоих каналов: сама модель «nf,ehtn» не узнаёт, опечатки ловят
оба канала. Температура по логлоссу на текстах из КТРУ, отказ при P < 1/2.

Вывод: цена 0.200 → 0.190, верно 268 → 254, отказ 4 → 35, неверно 28 → 11. Без отказа
точность 0.897 против 0.893 у бейзлайна: выигрыш только в честном отказе. rubert-tiny2 одна
давала 0.35 (весь текст) и 0.53 (по кускам), e5-small 0.82 и 0.87, USER-base не загрузился.
Температура на текстах с неоднозначным коротким названием выходила вдвое выше и давала 63
отказа (цена 0.250). Весь текст рядом с лучшим куском: 0.207, хуже. Ошибки: посторонний кусок
«производство Россия» тянет «Стол производственный», соседние шаблоны (детское и взрослое).
Ответ 50 мс на текст на 4 потоках CPU, индекс и калибровка 2 минуты при первом вызове, ~1 ГБ.
"""

import re
import zlib
from functools import cache
from random import Random

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.special import log_softmax, softmax
from sklearn.feature_extraction.text import TfidfVectorizer

from contracts.pipeline import Candidate
from contracts.ref import Position
from pipeline.text import russian

MODEL = "intfloat/multilingual-e5-small"
PREFIX = "query: "
CACHE = "data/processed/candidates_embeddings_{}_{:08x}.npy"
MIN_PROBA = 0.5
INDEXES: dict[tuple[str, ...], dict] = {}


def find(text: str, ref: list[Position]) -> list[Candidate]:
    finals = tuple(p for p in ref if p["position_type"] == "final")
    if not text.strip() or not finals:
        return []
    key = tuple(p["code"] for p in finals)
    if key not in INDEXES:
        INDEXES[key] = indexed(finals)
    index = INDEXES[key]
    proba = softmax(template_scores(index, [text])[0] / index["temperature"])
    best = proba.argmax()
    if proba[best] < MIN_PROBA:
        return []
    template = index["templates"][best]
    return [
        {"code": p["code"], "name": p["name"], "score": float(proba[best])}
        for p in finals
        if (p["parent_code"] or p["code"]) == template
    ]


def template_scores(index: dict, texts: list[str]) -> np.ndarray:
    fixed = [layout_fixed(t) for t in texts]
    semantic = np.array([fragment_similarity(index["vectors"], text) for text in fixed])
    lexical = (index["tfidf"].transform(fixed) @ index["matrix"].T).toarray()
    scores = (semantic + lexical) / 2
    return np.stack([scores[:, members].max(axis=1) for members in index["members"]], axis=1)


def fragment_similarity(vectors: np.ndarray, text: str) -> np.ndarray:
    """Название товара это один кусок текста: бренд и характеристики не размывают его смысл."""
    parts = [part.strip() for part in re.split(r"[,;\n]", text) if part.strip()]
    return (encode(parts or [text]) @ vectors.T).max(axis=0)


def layout_fixed(text: str) -> str:
    return " ".join(russian(word)[0] for word in text.split())


def indexed(finals: tuple[Position, ...]) -> dict:
    docs = [p["name"] for p in finals]
    templates = sorted({p["parent_code"] or p["code"] for p in finals})
    members = [
        [i for i, p in enumerate(finals) if (p["parent_code"] or p["code"]) == t] for t in templates
    ]
    tfidf = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True)
    index = {
        "templates": templates,
        "members": members,
        "tfidf": tfidf,
        "matrix": tfidf.fit_transform(docs),
        "vectors": cached_vectors(docs),
    }
    index["temperature"] = calibrated(index, finals, templates)
    return index


def cached_vectors(docs: list[str]) -> np.ndarray:
    path = CACHE.format(MODEL.split("/")[-1], zlib.crc32("\n".join(docs).encode()))
    try:
        return np.load(path)
    except FileNotFoundError:
        vectors = encode(docs)
        np.save(path, vectors)
        return vectors


def encode(texts: list[str]) -> np.ndarray:
    prefixed = [PREFIX + text for text in texts]
    return model().encode(prefixed, batch_size=16, normalize_embeddings=True)


@cache
def model():
    import torch
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(4)
    return SentenceTransformer(MODEL, device="cpu")


def calibrated(index: dict, finals: tuple[Position, ...], templates: list[str]) -> float:
    """Температура по логлоссу на текстах из самого КТРУ: часть слов названия и значения."""
    rng = Random(0)
    names = {}
    for position in finals:
        names.setdefault(position["name"].lower(), set()).add(
            position["parent_code"] or position["code"]
        )
    texts, gold = [], []
    for position in finals:
        texts.append(spoken(position, names, rng))
        gold.append(templates.index(position["parent_code"] or position["code"]))
    scores = template_scores(index, texts)
    rows = np.arange(len(gold))

    def loss(temperature: float) -> float:
        return -log_softmax(scores / temperature, axis=1)[rows, gold].mean()

    return minimize_scalar(loss, bounds=(0.001, 1), method="bounded").x


def spoken(position: Position, names: dict[str, set[str]], rng: Random) -> str:
    """Название без части слов, с потерянной буквой, вперемешку с парой значений.

    Слова названия выбрасываем, только если остаток всё ещё указывает на один шаблон:
    по неразличимому тексту температура учится сомневаться и там, где ответ однозначен.
    """
    name = position["name"].split()
    kept = sorted([0, *rng.sample(range(1, len(name)), rng.randint(0, len(name) - 1))])
    short = " ".join(name[i] for i in kept)
    if not unique(short, names):
        short = position["name"]
    if len(short) > 4:
        cut = rng.randrange(len(short))
        short = short[:cut] + short[cut + 1 :]
    values = [
        str(rng.choice(c["values"]))
        for c in position["characteristics"]
        if c["values"] and not isinstance(c["values"][0], dict)
    ]
    pieces = [short, *rng.sample(values, min(len(values), rng.randint(0, 3)))]
    rng.shuffle(pieces)
    return ", ".join(pieces)


def unique(short: str, names: dict[str, set[str]]) -> bool:
    words = set(short.lower().split())
    templates = set()
    for name, codes in names.items():
        if words <= set(name.split()):
            templates |= codes
    return len(templates) == 1
