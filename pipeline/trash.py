"""Новая характеристика или кусок текста: свойство товара или мусор.

Порядок: память решений модератора, затем контакты, затем zero-shot NLI. Новое знание
живёт в памяти, а не в весах: решение модератора действует сразу, без переобучения.
"""

import json
import re
import threading
from functools import cache
from pathlib import Path

from contracts.pipeline import Check, Decision, Entail, Verdict
from pipeline.explain import CHECK_WHY
from pipeline.text import stems

MODEL_ID = "cointegrated/rubert-base-cased-nli-threeway"
BASE_MODEL = Path("models/nli")
TUNED_MODEL = Path("models/nli-trash")
MODERATION = Path("data/moderation.json")
MAX_TOKENS = 64
PROPERTY = "Это свойство или характеристика товара."
NOT_PROPERTY = "Это реклама, контакты, цена или условия продажи, а не свойство товара."
# Пороги на вероятность свойства. Между ними спорное, его решает модератор. Подбирает
# scripts/eval_trash.py: выбрасываем молча только то, в чём модель почти не ошибается.
TRASH_BELOW = 0.15
PROPERTY_ABOVE = 0.7
# Доля основ нового куска, найденных в прошлом решении: с ней считаем его похожим.
SIMILAR = 0.5
CONTACT = re.compile(r"https?://|www\.|@|\+?\d[\d\s()-]{8,}\d|\.(?:ru|com|net|org)\b", re.I)

saving = threading.Lock()


def judge(told: list[str], product: str) -> list[Check]:
    return verdicts(told, product, decisions(), nli())


def verdicts(
    told: list[str], product: str, memory: list[Decision], entail: Entail | None
) -> list[Check]:
    found: list[Check | None] = [remembered(text, memory) for text in told]
    asked = [i for i, check in enumerate(found) if check is None]
    scores = property_scores([(product, told[i]) for i in asked], entail)
    for i, score in zip(asked, scores, strict=True):
        found[i] = scored(told[i], score, memory)
    return [check for check in found if check is not None]


def remembered(text: str, memory: list[Decision]) -> Check | None:
    key = stems(name_of(text))
    for decision in reversed(memory):
        if key and key in (stems(decision["told"]), stems(decision["name"])):
            return from_decision(decision, "approved", "rejected")
    if CONTACT.search(text):
        return check("trash", "contact")
    return None


def scored(text: str, score: float | None, memory: list[Decision]) -> Check:
    if score is None:
        return check("unsure", "no_model")
    if score < TRASH_BELOW:
        return check("trash", "trash", score)
    if score > PROPERTY_ABOVE:
        return check("property", "property", score)
    # Спорное подсказывает похожее прошлое решение: «прорезиненная ручка» после
    # одобренной «противоскользящей ручки». Название не берём: похожее не значит то же.
    near = nearest(text, memory)
    if near:
        found = from_decision(near, "similar_approved", "similar_rejected")
        found["score"], found["name"] = score, None
        return found
    return check("unsure", "unsure", score)


def nearest(text: str, memory: list[Decision]) -> Decision | None:
    key = stems(name_of(text))
    best, share = None, SIMILAR
    for decision in memory if key else []:
        for other in (stems(decision["told"]), stems(decision["name"])):
            if len(key & other) / len(key) >= share:
                best, share = decision, len(key & other) / len(key)
    return best


def from_decision(decision: Decision, approved: str, rejected: str) -> Check:
    verdict: Verdict = "property" if decision["approved"] else "trash"
    return check(verdict, approved if decision["approved"] else rejected, name=decision["name"])


def check(
    verdict: Verdict, reason: str, score: float | None = None, name: str | None = None
) -> Check:
    """Единое название нужно только тому, что остаётся характеристикой."""
    why = CHECK_WHY[reason].format(name=name or "", score=score or 0.0)
    kept = name if verdict == "property" else None
    return {"verdict": verdict, "why": why, "score": score, "name": kept}


def name_of(text: str) -> str:
    return text.split(":", 1)[0]


def premise(text: str, product: str) -> str:
    return f"{product}. {text}"


def property_scores(told: list[tuple[str, str]], entail: Entail | None) -> list[float | None]:
    """Zero-shot по парам (товар, кусок): обе гипотезы к одной посылке, вероятность свойства
    это доля следования гипотезы «свойство» среди двух."""
    if entail is None:
        return [None] * len(told)
    pairs = []
    for product, text in told:
        pairs += [(premise(text, product), PROPERTY), (premise(text, product), NOT_PROPERTY)]
    probs = entail(pairs) if pairs else []
    scores: list[float | None] = []
    for yes, no in zip(probs[::2], probs[1::2], strict=True):
        scores.append(yes / (yes + no) if yes + no else 0.5)
    return scores


def decisions(path: Path = MODERATION) -> list[Decision]:
    # Файл читаем на каждый запрос: решение модератора действует со следующего запроса.
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def decide(decision: Decision, path: Path = MODERATION) -> None:
    with saving:
        memory = decisions(path)
        memory.append(decision)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(memory, ensure_ascii=False, indent=1), encoding="utf-8")


@cache
def nli(path: Path | None = None, device: str = "cpu") -> Entail | None:
    """Модель с диска: дообученная, если есть, иначе исходная. Без модели или без torch
    пайплайн работает, а всё новое уходит модератору. Пайплайн на CPU: модели хватает,
    а видеокарту может занимать обучение."""
    path = path or (TUNED_MODEL if TUNED_MODEL.exists() else BASE_MODEL)
    if not path.exists():
        return None
    try:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
    except ImportError:
        return None
    tokenizer = AutoTokenizer.from_pretrained(path)
    model = AutoModelForSequenceClassification.from_pretrained(path).to(device).eval()
    entailment = entailment_id(model.config.id2label)

    def entail(pairs: list[tuple[str, str]]) -> list[float]:
        batch = tokenizer(
            [p for p, _ in pairs],
            [h for _, h in pairs],
            padding=True,
            truncation=True,
            max_length=MAX_TOKENS,
            return_tensors="pt",
        ).to(device)
        with torch.inference_mode():
            return model(**batch).logits.softmax(-1)[:, entailment].tolist()

    return entail


def entailment_id(labels: dict[int, str], prefix: str = "entail") -> int:
    return next(i for i, label in labels.items() if label.lower().startswith(prefix))
