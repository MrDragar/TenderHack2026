"""Замер проверки на мусор и подбор порогов. Тяжёлый: запускать на хосте с моделью.

Примеры из scripts/trash_examples.py, по умолчанию dev-часть. Пороги подбираем так, чтобы
молча выброшенное почти всегда было мусором, а уверенно принятое почти всегда свойством:
ошибку в спорной зоне поправит модератор, ошибку на краях не увидит никто.
"""

import argparse
import time
from pathlib import Path

from pipeline.trash import PROPERTY_ABOVE, TRASH_BELOW, nli, property_scores
from scripts.dataloader import load_data, load_portal
from scripts.trash_examples import examples

BATCH = 64
TRASH_PRECISION = 0.97
PROPERTY_PRECISION = 0.9


def auc(scored: list[tuple[float, bool]]) -> float:
    """Доля пар «свойство, мусор», где у свойства оценка выше."""
    ranked = sorted(scored)
    positives = sum(label for _, label in ranked)
    negatives = len(ranked) - positives
    seen_negatives = 0
    wins = 0
    for _, label in ranked:
        if label:
            wins += seen_negatives
        else:
            seen_negatives += 1
    return wins / (positives * negatives) if positives and negatives else 0.0


def trash_threshold(scored: list[tuple[float, bool]]) -> float:
    """Самый высокий порог, ниже которого мусора не меньше TRASH_PRECISION."""
    best = 0.0
    below = 0
    trash = 0
    for score, label in sorted(scored):
        below += 1
        trash += not label
        if trash / below >= TRASH_PRECISION:
            best = score
    return best


def property_threshold(scored: list[tuple[float, bool]]) -> float:
    """Самый низкий порог, выше которого свойств не меньше PROPERTY_PRECISION."""
    best = 1.0
    above = 0
    good = 0
    for score, label in sorted(scored, reverse=True):
        above += 1
        good += label
        if good / above >= PROPERTY_PRECISION:
            best = score
    return best


def table(scored: list[tuple[float, bool]], low: float, high: float) -> None:
    for label, title in ((True, "свойство"), (False, "мусор")):
        part = [score for score, is_property in scored if is_property == label]
        trash = sum(score < low for score in part)
        good = sum(score > high for score in part)
        unsure = len(part) - trash - good
        print(
            f"  {title:9} n={len(part):5}  выброшено {trash / len(part):6.1%}  "
            f"модератору {unsure / len(part):6.1%}  принято {good / len(part):6.1%}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, help="папка модели, по умолчанию как в пайплайне")
    parser.add_argument("--limit", type=int, default=2000)
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()

    entail = nli(args.model)
    if entail is None:
        raise SystemExit("Модели нет: uv run python -m scripts.download_nli")
    found = examples(load_data(), load_portal(), [], test=args.test, seed=1)[: args.limit]
    scored = []
    started = time.perf_counter()
    for start in range(0, len(found), BATCH):
        chunk = found[start : start + BATCH]
        scores = property_scores([(product, told) for product, told, _ in chunk], entail)
        labels = [label for _, _, label in chunk]
        scored += [(score or 0.0, label) for score, label in zip(scores, labels, strict=True)]
    spent = (time.perf_counter() - started) / max(len(found), 1)

    low, high = trash_threshold(scored), property_threshold(scored)
    print(f"примеров {len(scored)}, {spent * 1000:.0f} мс на пример, AUC {auc(scored):.3f}")
    print(f"сейчас TRASH_BELOW={TRASH_BELOW}, PROPERTY_ABOVE={PROPERTY_ABOVE}:")
    table(scored, TRASH_BELOW, PROPERTY_ABOVE)
    print(f"предлагаем TRASH_BELOW={low:.2f}, PROPERTY_ABOVE={max(high, low):.2f}:")
    table(scored, low, max(high, low))


if __name__ == "__main__":
    main()
