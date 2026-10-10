"""Примеры «свойство или мусор» для дообучения и замера без ручной разметки.

Свойство: характеристика КТРУ или портала рядом со своим товаром, одобренное модератором.
Мусор: характеристика чужой категории, контакты, цены и условия продажи, бессмыслица,
отклонённое модератором. Слова торга здесь только как материал для примеров, решение
пайплайна на них не опирается.
"""

import json
import sys
from random import Random

from contracts.pipeline import Decision
from contracts.ref import Characteristic, Portal, Position
from pipeline.text import stems
from pipeline.values import tidy
from scripts.generate import ALIAS, held_out, numbers, renderable

type Example = tuple[str, str, bool]

CHARS_PER_POSITION = 2
DEAL = [
    "продам",
    "продаю срочно",
    "торг уместен",
    "недорого",
    "самовывоз",
    "доставка по городу",
    "оптом и в розницу",
    "оплата наличными",
    "цена договорная",
    "звоните",
    "пишите в личку",
    "обращайтесь",
    "скидка",
    "распродажа",
    "предоплата",
    "бесплатно",
]
LETTERS = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"


def examples(
    ref: list[Position],
    portal: Portal,
    memory: list[Decision],
    test: bool = False,
    seed: int = 0,
) -> list[Example]:
    rng = Random(seed)
    finals = [p for p in ref if p["position_type"] == "final" and held_out(p["code"]) == test]
    found = properties(finals, rng) + portal_properties(portal, test, rng)
    found += foreign(finals, rng, len(found) // 3)
    found += [deal(rng, finals) for _ in range(len(found) // 6)]
    found += [(d["product"], d["told"], d["approved"]) for d in memory]
    rng.shuffle(found)
    return found


def properties(finals: list[Position], rng: Random) -> list[Example]:
    found = []
    for position in finals:
        usable = [char for char in position["characteristics"] if renderable(char)]
        for char in rng.sample(usable, min(CHARS_PER_POSITION, len(usable))):
            value = value_text(char, rng)
            found.append((position["name"], f"{char['name']}: {value}", True))
            # Остаток текста это отдельные слова, поэтому учим и значения без названия.
            if char["type"] == "text":
                found.append((position["name"], value, True))
    return found


def value_text(char: Characteristic, rng: Random) -> str:
    if char["type"] == "number" and "unit" in char:
        return f"{tidy(rng.choice(numbers(char)))} {ALIAS[char['unit']['code']]}"
    return rng.choice([v for v in char["values"] if isinstance(v, str) and v != "Нет"])


def portal_properties(portal: Portal, test: bool, rng: Random) -> list[Example]:
    found = []
    for section, chars in portal.items():
        if held_out(section) != test:
            continue
        for char in rng.sample(chars, min(CHARS_PER_POSITION, len(chars))):
            told = char["name"]
            if char["values"]:
                told = f"{told}: {rng.choice(char['values'])}"
            found.append((section, told, True))
    return found


def foreign(finals: list[Position], rng: Random, count: int) -> list[Example]:
    """Свойство товара из чужой категории: «Стол письменный. Размер воротника: 42». Берём
    только названия, ни одного слова которых нет у своей категории: «Цвет» есть у всех."""
    vocab: dict[str, set[str]] = {}
    for position in finals:
        for char in position["characteristics"]:
            vocab.setdefault(position["category"], set()).update(stems(char["name"]))
    found: list[Example] = []
    for _ in range(count * 5):
        if len(found) >= count:
            break
        own, other = rng.sample(finals, 2)
        if own["category"] == other["category"]:
            continue
        usable = [char for char in other["characteristics"] if renderable(char)]
        usable = [c for c in usable if not stems(c["name"]) & vocab[own["category"]]]
        if usable:
            char = rng.choice(usable)
            found.append((own["name"], f"{char['name']}: {value_text(char, rng)}", False))
    return found


def deal(rng: Random, finals: list[Position]) -> Example:
    product = rng.choice(finals)["name"] if finals else "Товар"
    phone = "+7" + "".join(rng.choice("0123456789") for _ in range(10))
    gibberish = "".join(rng.choice(LETTERS) for _ in range(rng.randint(5, 10)))
    told = rng.choice(
        [
            rng.choice(DEAL),
            f"Телефон: {phone}",
            f"Цена: {rng.randint(100, 50000)} руб",
            f"сайт: shop{rng.randint(1, 999)}.ru",
            f"почта: sale{rng.randint(1, 999)}@mail.ru",
            gibberish,
        ]
    )
    return product, told, False


if __name__ == "__main__":
    from scripts.dataloader import load_data, load_portal

    count = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    sample = examples(load_data(), load_portal(), [])[:count]
    print(json.dumps(sample, ensure_ascii=False, indent=1))
