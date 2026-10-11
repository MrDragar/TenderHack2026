"""Форма слова и опечатка это одна модель правок подстрок с позицией, выученная на листах 2.

Вывод: на dev верно 564, отказ 72, неверно 50 (бейзлайн 486/117/83), стало лучше 105, хуже
33; noisy_channel на прошлом прогоне 552/83/48, лучше 98, хуже 35. По тем же примерам против
noisy_channel: отказ → верно 9 (двойная порча «днтскые», «жинская», опечатки в коротких
словах «прмое», «деррево»), верно → неверно 1 («резина» вместо «резинка» названа формой).
Опечатки найдены 46 из 48 (было 38), ложная «опечатка» у чистого значения 7 (было 6): формы
генератора вроде «металлическые», «распашное», чьих окончаний нет в листах 2. Без шума,
знающего словарь, честная цена правки дороже шума: 547/92/43, опечатки 30 из 48.

Brill и Moore 2000: P(слово текста | слово значения) это самое дешёвое разбиение слова
значения на куски α, каждый из которых стал куском β, цена −log count(α→β) / count(α).
Граница слова ^ и $ входит в α, поэтому «ый$ → ая$» и «ы → а» в середине разные правки.
Правила берутся из пар листа 2 (написание базовой характеристики против значения связанной
эталонной): выравнивание по Левенштейну, кусок правок с соседними шагами до двух, доля
счёта на каждое расширение. Правка одной буквы ещё может стоить как частота своего вида.
Раскладка отдельная порча. Форма слова это одна выученная правка конца слова, иначе опечатка.
Шум пишет только слова поставщиков из листа 1, слово не из словаря для шума тоже правка.
"""

import json
from collections import Counter
from difflib import SequenceMatcher
from functools import cache
from math import exp, inf, log
from pathlib import Path
from typing import Literal, TypedDict

from openpyxl import load_workbook

from contracts.pipeline import Candidate, FixKind, FoundChar, Fragment, Rule
from contracts.ref import Characteristic, Portal, PortalChar, Position, Unit, Value
from pipeline.explain import mark
from pipeline.text import Word, stem, stems, words
from pipeline.values import UNIT_ALIASES, bounds, convert, same, scale, show, tidy
from scripts.products import RAW_DIR, SHEETS, products

# Перепутанная единица чаще всего сдвигает число на порядок: см вместо мм, м вместо см.
SHIFTS = (10, 0.1, 100, 0.01, 1000, 0.001)
SHOWN_OPTIONS = 6
PAIRS_PATH = Path("data/processed/link_substring_edits_pairs.json")
# Написание базовой характеристики сравниваем с эталонным, только если это одно значение.
SAME_VALUE = 0.5
KINDS = ("clean", "layout", "changed", "noise")
# Правка до трёх букв вместе с границей слова, контекст до двух соседних шагов, как у Brill и Moore.
MAX_RULE = 4
CONTEXT = 2
ALPHABET = 34

type Source = Literal["ktru", "portal"]


class Option(TypedDict):
    char: Characteristic
    value: str
    source: Source


class Explained(TypedDict):
    option: Option
    gain: float
    used: set[int]
    named: set[int]
    kind: FixKind | None


def link(
    fragments: list[Fragment],
    candidates: list[Candidate],
    section: str | None,
    ref: list[Position],
    portal: Portal,
) -> list[FoundChar]:
    by_code = {p["code"]: p for p in ref}
    positions = [by_code[candidate["code"]] for candidate in candidates]
    chars = [char for position in positions for char in position["characteristics"]]
    allowed = allowed_values(chars)
    portal_chars = [as_ktru(char) for char in portal.get(section or "", [])]
    product = stems(" ".join(c["name"] for c in candidates))
    prior = value_prior(positions)

    pairs = told_pairs(fragments, chars, portal_chars, allowed, product, prior)
    shared = set.intersection(*(stems(c["name"]) for c in candidates)) if candidates else set()
    values = told_values(fragments, chars, portal_chars, product, shared, prior)
    numbers = number_values(fragments, chars, portal_chars, allowed, product)
    found = choose(pairs, values, numbers)
    # Обязательность у соседних кодов разная: обязательна, если обязательна хоть у одного.
    required = {char["name"] for char in chars if char["required"]}
    for char in found:
        char["required"] = char["source"] == "ktru" and char["name"] in required
    return found


def choose(
    pairs: list[FoundChar], values: list[FoundChar], numbers: list[FoundChar]
) -> list[FoundChar]:
    """Явная пара сильнее значения без названия, оно сильнее числа. Значения одного правила
    уже выбраны каналом, между правилами разные значения дают правку «два значения»."""
    by_name: dict[str, list[FoundChar]] = {}
    for rule in (pairs, values, numbers):
        mine: dict[str, list[FoundChar]] = {}
        for char in rule:
            mine.setdefault(char["name"], []).append(char)
        for name, chars in mine.items():
            kept = by_name.get(name)
            if kept is None:
                by_name[name] = chars
                continue
            other = [c for c in chars if not any(same(k["value"], c["value"]) for k in kept)]
            if other:
                mark(kept[0], "conflict", other=other[0]["fragment"])
    return [char for chars in by_name.values() for char in chars]


@cache
def word_pairs() -> dict[str, dict[str, int]]:
    """Слова написания поставщика против слов эталонного значения, один раз по листам 2."""
    if not PAIRS_PATH.exists():
        PAIRS_PATH.write_text(json.dumps(mined_pairs(), ensure_ascii=False, indent=1))
    return json.loads(PAIRS_PATH.read_text())


@cache
def model() -> tuple[dict[str, float], dict[str, dict[str, float]], dict[str, float]]:
    """Цены видов слова и правок подстрок −log P(β | α) = −log count(α→β) / count(α), где α
    считается в словах значений, которые поставщики изменили (Brill и Moore 2000). Правка
    одной буквы ещё и по виду: частота вида на букву, у редкой буквы своих правок мало."""
    counts = word_pairs()
    seen = Counter(counts["words"])
    said = sum(seen[kind] for kind in KINDS)
    price = {kind: -log((seen[kind] + 1) / said) for kind in KINDS}
    price["omit"] = -log((seen["omit"] + 1) / (seen["kept"] + seen["omit"]))
    found: Counter[tuple[str, str]] = Counter()
    by_kind: Counter[str] = Counter()
    for key, times in counts["changed"].items():
        written, meant = key.split(" ")
        ops = aligned(f"^{written}$", f"^{meant}$")
        for rule, share in expanded(ops):
            found[rule] += share * times
        for kind in letter_kinds(ops):
            by_kind[kind] += times
        by_kind["letter"] += len(meant) * times
        by_kind["gap"] += (len(meant) + 1) * times
    contexts: Counter[str] = Counter()
    alphas = {alpha for alpha, _ in found}
    for key, times in counts["changed"].items():
        meant = "^" + key.split(" ")[1] + "$"
        for size in range(MAX_RULE + 1):
            for i in range(len(meant) - size + 1):
                if meant[i : i + size] in alphas:
                    contexts[meant[i : i + size]] += times
    rules: dict[str, dict[str, float]] = {}
    for (alpha, beta), count in found.items():
        # Сглаживание на алфавит: правка из одного примера при редком α не бесплатна.
        rules.setdefault(alpha, {})[beta] = -log(count / (contexts[alpha] + ALPHABET))
    backoff = {
        "del": -log((by_kind["del"] + 1) / by_kind["letter"]),
        "swap": -log((by_kind["swap"] + 1) / by_kind["letter"]),
        "sub": -log((by_kind["sub"] + 1) / by_kind["letter"]) + log(ALPHABET - 1),
        "ins": -log((by_kind["ins"] + 1) / by_kind["gap"]) + log(ALPHABET),
    }
    return price, rules, backoff


def letter_kinds(ops: list[tuple[str, str]]) -> list[str]:
    """Виды правок одной буквы в выравнивании; две замены крест-накрест это перестановка."""
    kinds = []
    i = 0
    while i < len(ops):
        meant, said = ops[i]
        if meant == said:
            i += 1
            continue
        after = ops[i + 1] if i + 1 < len(ops) else ("", "")
        if meant and said and after == (said, meant):
            kinds.append("swap")
            i += 2
            continue
        kinds.append("sub" if meant and said else "del" if meant else "ins")
        i += 1
    return kinds


@cache
def noise(said: str) -> float:
    """Слово шума это порча «лишнее слово» и любое слово из тех, что поставщики пишут в товарах
    листа 1, на выбор. Слова не из словаря шум сам не пишет, только с правкой: она стоит
    не меньше изменённой записи и самой дешёвой правки буквы."""
    price, _, backoff = model()
    cost = price["noise"] + log(len(vocabulary()))
    if said in vocabulary():
        return cost
    return cost + price["changed"] + min(backoff.values())


@cache
def vocabulary() -> set[str]:
    found = set()
    for product in products({}, build=False):
        found |= {word["text"] for word in words(product["name"])}
        for name, value in product["pairs"]:
            found |= {word["text"] for word in words(f"{name} {value}")}
    return found


def mined_pairs() -> dict[str, Counter[str]]:
    written: Counter[tuple[str, tuple[str, ...]]] = Counter()
    for sheet in SHEETS:
        book = load_workbook(RAW_DIR / sheet, read_only=True)
        for row in book.worksheets[1].iter_rows(min_row=2, max_col=11, values_only=True):
            linked = tuple(linked_values(str(row[10] or "")))
            if row[1] and row[6] and linked:
                for value in split_values(str(row[6])):
                    written[value, linked] += 1
        book.close()
    seen: Counter[str] = Counter()
    changed: Counter[str] = Counter()
    for (value, linked), times in written.items():
        count_words(value, linked, times, seen, changed)
    return {"words": seen, "changed": changed}


def split_values(cell: str) -> list[str]:
    return [part.strip() for part in cell.split(";") if part.strip()]


def linked_values(cell: str) -> list[str]:
    """«5517160: М0; М1 | 5517205: М0; М1» → значения эталонных характеристик."""
    found = []
    for part in cell.split(" | "):
        found += split_values(part.split(":", 1)[-1])
    return found


def count_words(
    value: str, linked: tuple[str, ...], times: int, seen: Counter, changed: Counter
) -> None:
    folded = value.casefold()
    best = max(linked, key=lambda v: SequenceMatcher(None, folded, v.casefold()).ratio())
    if SequenceMatcher(None, folded, best.casefold()).ratio() < SAME_VALUE:
        return
    meant = [word["text"] for word in words(best)]
    hit = set()
    for word in words(value):
        ranked = [(closeness(word, target), i) for i, target in enumerate(meant)]
        ranked = [(rank, i) for rank, i in ranked if rank is not None]
        if not ranked:
            seen["noise"] += times
            continue
        rank, index = min(ranked)
        hit.add(index)
        if rank > 1:
            seen["changed"] += times
            changed[f"{word['text']} {meant[index]}"] += times
        else:
            seen["layout" if rank else "clean"] += times
    seen["kept"] += len(hit) * times
    seen["omit"] += (len(meant) - len(hit)) * times


def closeness(word: Word, target: str) -> int | None:
    """0 то же слово, 1 раскладка, дальше 2 плюс число правок букв; None, если слово другое."""
    if word["text"] == target:
        return 1 if word["fix"] else 0
    same_stem = word["stem"] == stem(target)
    if not same_stem and abs(len(word["text"]) - len(target)) > 2:
        return None
    distance = sum(a != b for a, b in aligned(word["text"], target))
    if same_stem or (distance <= 2 and len(target) >= 4):
        return 2 + distance
    return None


def aligned(said: str, meant: str) -> list[tuple[str, str]]:
    """Выравнивание по Левенштейну: пары (буква КТРУ, буква текста), пустая строка это пропуск."""
    rows, cols = len(meant) + 1, len(said) + 1
    table = [[i + j if i == 0 or j == 0 else 0 for j in range(cols)] for i in range(rows)]
    for i in range(1, rows):
        for j in range(1, cols):
            table[i][j] = min(
                table[i - 1][j] + 1,
                table[i][j - 1] + 1,
                table[i - 1][j - 1] + (meant[i - 1] != said[j - 1]),
            )
    ops = []
    i, j = rows - 1, cols - 1
    while i or j:
        if i and j and table[i][j] == table[i - 1][j - 1] + (meant[i - 1] != said[j - 1]):
            ops.append((meant[i - 1], said[j - 1]))
            i, j = i - 1, j - 1
        elif i and table[i][j] == table[i - 1][j] + 1:
            ops.append((meant[i - 1], ""))
            i -= 1
        else:
            ops.append(("", said[j - 1]))
            j -= 1
    return ops[::-1]


def expanded(ops: list[tuple[str, str]]) -> list[tuple[tuple[str, str], float]]:
    """Каждый кусок подряд идущих правок с соседними шагами выравнивания, до CONTEXT шагов,
    с долей счёта: полезный контекст копит счёт по многим парам, случайный нет."""
    found = []
    i = 0
    while i < len(ops):
        if ops[i][0] == ops[i][1]:
            i += 1
            continue
        end = i
        while end < len(ops) and ops[end][0] != ops[end][1]:
            end += 1
        rules = set()
        for left in range(CONTEXT + 1):
            for right in range(CONTEXT + 1 - left):
                piece = ops[max(i - left, 0) : end + right]
                alpha = "".join(a for a, _ in piece)
                beta = "".join(b for _, b in piece)
                if len(alpha) <= MAX_RULE and len(beta) <= MAX_RULE:
                    rules.add((alpha, beta))
        found += [(rule, 1 / len(rules)) for rule in rules]
        i = end
    return found


@cache
def word_cost(said: str, layout: bool, meant: str) -> tuple[float, FixKind | None]:
    """−log P(слово текста | слово значения): чисто или одна изменённая запись, цена которой
    это самое дешёвое разбиение на правки подстрок. Форма слова это одна выученная правка
    конца слова, остальное опечатка."""
    price = model()[0]
    if said == meant:
        cost, kind = price["clean"], None
    else:
        cost = price["changed"] + channel(f"^{said}$", f"^{meant}$")
        kind = None if inflected(said, meant) else "typo"
    if layout:
        return cost + price["layout"] - price["clean"], "layout"
    return cost, kind


def channel(said: str, meant: str) -> float:
    """Самое дешёвое разбиение meant на куски, каждый из которых стал куском said. Совпавшая
    буква бесплатна: цена «слово изменено» уже взята. Правка одной буквы или перестановка
    соседних, кроме своей цены, может стоить как частота своего вида."""
    _, rules, backoff = model()
    table = [[inf] * (len(said) + 1) for _ in range(len(meant) + 1)]
    table[0][0] = 0.0
    for i in range(len(meant) + 1):
        for j in range(len(said) + 1):
            here = table[i][j]
            if here == inf:
                continue
            moves = []
            if i < len(meant) and j < len(said) and meant[i] == said[j]:
                moves.append((1, 1, 0.0))
            for size in range(min(MAX_RULE, len(meant) - i) + 1):
                for beta, cost in rules.get(meant[i : i + size], {}).items():
                    if said.startswith(beta, j):
                        moves.append((size, len(beta), cost))
            for kind, alpha, beta in letter_edits(said, meant, i, j):
                moves.append((len(alpha), len(beta), backoff[kind]))
            for size, length, cost in moves:
                if here + cost < table[i + size][j + length]:
                    table[i + size][j + length] = here + cost
    return table[-1][-1]


def letter_edits(said: str, meant: str, i: int, j: int) -> list[tuple[str, str, str]]:
    """Замена, пропуск, вставка буквы и перестановка соседних; границы слова не правятся."""
    one = meant[i : i + 1] not in ("", "^", "$")
    other = said[j : j + 1] not in ("", "^", "$")
    edits = []
    if other:
        edits.append(("ins", "", said[j]))
    if one:
        edits.append(("del", meant[i], ""))
    if one and other and meant[i] != said[j]:
        edits.append(("sub", meant[i], said[j]))
    two = meant[i : i + 2]
    if len(two) == 2 and two[::-1] == said[j : j + 2] and two[0] != two[1] and "$" not in two:
        edits.append(("swap", two, two[::-1]))
    return edits


def inflected(said: str, meant: str) -> bool:
    """said получается из meant одной выученной правкой конца слова: «ый$ → ая$»."""
    rules = model()[1]
    meant, said = f"^{meant}$", f"^{said}$"
    for size in range(1, MAX_RULE + 1):
        prefix = meant[:-size]
        if said.startswith(prefix) and said[len(prefix) :] in rules.get(meant[-size:], {}):
            return True
    return False


def value_prior(positions: list[Position]) -> dict[tuple[str, str], float]:
    """−log доли кандидатов, у которых есть это значение, среди тех, у кого есть характеристика."""
    having: Counter[str] = Counter()
    valued: Counter[tuple[str, str]] = Counter()
    for position in positions:
        for char in position["characteristics"]:
            having[char["name"]] += 1
            for value in {v for v in char["values"] if isinstance(v, str)}:
                valued[char["name"], value] += 1
    return {key: -log(count / having[key[0]]) for key, count in valued.items()}


def explain(
    fragment_words: list[Word],
    options: list[Option],
    prior: dict[tuple[str, str], float],
    product: set[str],
) -> list[Explained]:
    """Жадно объясняем текст: значение с наибольшим выигрышем над шумом забирает свои слова
    и слова названия своей характеристики, остальные пересчитываются на оставшихся. Слова,
    общие для названий всех кандидатов, уже объяснены товаром. Если соперник за те же слова
    почти так же вероятен (P лучшего < 1/2), слова забираем, а ответа не даём."""
    taken = {i for i, word in enumerate(fragment_words) if word["stem"] in product}
    values: set[int] = set()
    found = []
    while True:
        scored = []
        for option in options:
            match = score(fragment_words, option, taken, values, prior, log(len(options)))
            if match:
                scored.append(match)
        if not scored:
            return found
        best = max(scored, key=lambda s: s["gain"])
        rivals = [s for s in scored if s is not best and s["used"] & best["used"]]
        odds = exp(-best["gain"]) + sum(exp(s["gain"] - best["gain"]) for s in rivals)
        taken |= best["used"] | best["named"]
        values |= best["used"]
        if 1 / (1 + odds) >= 0.5:
            found.append(best)


def score(
    fragment_words: list[Word],
    option: Option,
    taken: set[int],
    values: set[int],
    prior: dict[tuple[str, str], float],
    choice: float,
) -> Explained | None:
    """Выигрыш значения над шумом: −log P(z | шум) − (−log P(z | a) − log P(a)), где P(a)
    это выбор одного из значений кандидатов и доля кандидатов с этим значением."""
    price = model()[0]
    meant = [word["text"] for word in words(searched_text(option["char"], option["value"]))]
    if not meant:
        return None
    gain = -choice - prior.get((option["char"]["name"], option["value"]), 0.0)
    used: set[int] = set()
    kinds = []
    for target in meant:
        place, saving, kind = closest(fragment_words, target, taken | used)
        if place is None:
            gain -= price["omit"]
            continue
        used.add(place)
        gain += saving
        kinds.append(kind)
    if not used:
        return None
    named: set[int] = set()
    if option["value"] != "Да":
        for target in {word["text"] for word in words(option["char"]["name"])}:
            place, saving, _ = closest(fragment_words, target, values | used | named)
            if place is not None:
                named.add(place)
                gain += saving
        if option["source"] == "portal" and not named:
            return None
    if gain <= 0:
        return None
    kind = "layout" if "layout" in kinds else "typo" if "typo" in kinds else None
    return {"option": option, "gain": gain, "used": used, "named": named, "kind": kind}


def closest(
    fragment_words: list[Word], target: str, busy: set[int]
) -> tuple[int | None, float, FixKind | None]:
    """Слово текста, которое словом target объясняется дешевле, чем шумом, и выигрыш."""
    best: tuple[int | None, float, FixKind | None] = (None, 0.0, None)
    for i, word in enumerate(fragment_words):
        if i in busy:
            continue
        cost, kind = word_cost(word["text"], word["fix"] == "layout", target)
        if noise(word["text"]) - cost > best[1]:
            best = (i, noise(word["text"]) - cost, kind)
    return best


def options_of(chars: list[Characteristic], source: Source) -> list[Option]:
    seen = set()
    found: list[Option] = []
    for char in chars:
        for value in char["values"]:
            if isinstance(value, str) and value != "Нет" and (char["name"], value) not in seen:
                seen.add((char["name"], value))
                found.append({"char": char, "value": value, "source": source})
    return found


def chosen(
    explained: list[Explained],
    item: Fragment,
    fragment_words: list[Word],
    rule: Rule,
    multiple: set[str],
) -> list[FoundChar]:
    """Одно значение на характеристику, несколько только там, где КТРУ разрешает выбор."""
    found: list[FoundChar] = []
    for match in explained:
        option = match["option"]
        name = option["char"]["name"]
        if any(f["name"] == name for f in found) and name not in multiple:
            continue
        start = min(fragment_words[i]["start"] for i in match["used"])
        end = max(fragment_words[i]["end"] for i in match["used"])
        char = found_char(
            option["char"],
            option["value"],
            option["source"],
            rule,
            item["text"][start:end],
            item["start"] + start,
            item["start"] + end,
        )
        if match["kind"]:
            mark(char, match["kind"])
        found.append(char)
    return found


def found_char(
    char: Characteristic,
    value: str | int | float,
    source: Source,
    rule: Rule,
    fragment: str,
    start: int = 0,
    end: int = 0,
    unit: Unit | None = None,
) -> FoundChar:
    return {
        "name": char["name"],
        "value": value,
        "unit": unit,
        "source": source,
        "fragment": fragment,
        "start": start,
        "end": end,
        "required": char["required"],
        "fix": None,
        "kind": None,
        "rule": rule,
    }


def as_ktru(char: PortalChar) -> Characteristic:
    """Характеристика портала в виде КТРУ, чтобы искать и переводить единицы одним кодом."""
    numeric = "числов" in char["type"].lower()
    item: Characteristic = {
        "code": "",
        "name": char["name"],
        "type": "number" if numeric else "text",
        "selection": "single",
        "required": False,
        "values": [] if numeric else list(char["values"]),
    }
    unit = (char["unit"] or "").strip("() ")
    if unit:
        item["unit"] = {"code": UNIT_ALIASES.get(unit.lower(), unit), "name": unit}
    return item


def allowed_values(chars: list[Characteristic]) -> dict[str, list[Value]]:
    """Значения характеристики по всем кандидатам: у соседних кодов списки разные."""
    allowed: dict[str, list[Value]] = {}
    for char in chars:
        allowed.setdefault(char["name"], []).extend(char["values"])
    return allowed


def multiple_names(chars: list[Characteristic]) -> set[str]:
    return {char["name"] for char in chars if char["selection"] == "multiple"}


def told_pairs(
    fragments: list[Fragment],
    chars: list[Characteristic],
    portal_chars: list[Characteristic],
    allowed: dict[str, list[Value]],
    product: set[str],
    prior: dict[tuple[str, str], float],
) -> list[FoundChar]:
    """Человек дописывает исправление в конец, поэтому из явных значений верны последние."""
    last: dict[str, list[FoundChar]] = {}
    for item in fragments:
        if item["form"] == "pair":
            found = pair_chars(item, chars, portal_chars, allowed, product, prior)
            if found:
                last[found[0]["name"]] = found
    return [char for found in last.values() for char in found]


def pair_chars(
    item: Fragment,
    chars: list[Characteristic],
    portal_chars: list[Characteristic],
    allowed: dict[str, list[Value]],
    product: set[str],
    prior: dict[tuple[str, str], float],
) -> list[FoundChar]:
    offset = name_offset(item["name"] or "", product)
    if offset is None:
        return []
    name = (item["name"] or "")[offset:]
    start = item["start"] + offset
    fragment = item["text"][offset:]
    char = named(chars, name)
    source: Source = "ktru"
    if char is None:
        char = named(portal_chars, name)
        source = "portal"
    if char is None:
        return [new_char(name, item["value"], fragment, start, item["end"])]
    values = allowed.get(char["name"], []) if source == "ktru" else char["values"]
    if char["type"] == "number":
        if item["number"] is None:
            return []
        found = [number_char(char, source, "pair", item["number"], item["unit"], values, fragment)]
    else:
        whole = char.copy()
        whole["values"] = values
        found = text_pair(whole, source, item["value"], fragment, prior, multiple_names(chars))
    for char_found in found:
        char_found["start"], char_found["end"] = start, item["end"]
    return found


def name_offset(name: str, product: set[str]) -> int | None:
    """«Джемпер рост: 170»: слова товара перед названием характеристики к нему не относятся."""
    for word in words(name):
        if word["stem"] not in product:
            return word["start"]
    return None


def named(chars: list[Characteristic], name: str) -> Characteristic | None:
    """Характеристика с тем же названием, иначе та, в названии которой есть все слова."""
    for char in chars:
        if char["name"].casefold() == name.casefold():
            return char
    wanted = stems(name)
    for char in chars:
        if wanted and wanted <= stems(char["name"]):
            return char
    return None


def new_char(name: str, raw: str, fragment: str, start: int, end: int) -> FoundChar:
    return {
        "name": name[:1].upper() + name[1:],
        "value": raw,
        "unit": None,
        "source": "new",
        "fragment": fragment,
        "start": start,
        "end": end,
        "required": False,
        "fix": None,
        "kind": None,
        "rule": "pair",
    }


def text_pair(
    char: Characteristic,
    source: Source,
    raw: str,
    fragment: str,
    prior: dict[tuple[str, str], float],
    multiple: set[str],
) -> list[FoundChar]:
    """Значение пары это те значения списка, к которым текст приводится дешевле шума. Списки
    портала собраны из того, что вводили поставщики, поэтому «нет в списке» только про КТРУ."""
    options = {v.casefold(): v for v in char["values"] if isinstance(v, str)}
    if raw.casefold() in options:
        return [found_char(char, options[raw.casefold()], source, "pair", fragment)]
    item: Fragment = {
        "form": "words",
        "name": None,
        "value": raw,
        "text": raw,
        "number": None,
        "unit": None,
        "start": 0,
        "end": len(raw),
    }
    raw_words = words(raw)
    pair_options = [o for o in options_of([char], source) if o["value"] != "Да"]
    found = chosen(
        explain(raw_words, pair_options, prior, set()), item, raw_words, "pair", multiple
    )
    if found:
        for char_found in found:
            char_found["fragment"] = fragment
        return found
    left = found_char(char, raw, source, "pair", fragment)
    if source == "ktru":
        shown = ", ".join(sorted(set(options.values()))[:SHOWN_OPTIONS])
        mark(left, "not_in_list", options=shown)
    return [left]


def told_values(
    fragments: list[Fragment],
    chars: list[Characteristic],
    portal_chars: list[Characteristic],
    product: set[str],
    shared: set[str],
    prior: dict[tuple[str, str], float],
) -> list[FoundChar]:
    """Значения без названия: «деревянный» → Тип каркаса: Деревянный. Значение портала берём,
    только если рядом названа и сама характеристика: в списках портала много мусора."""
    ktru = options_of(chars, "ktru")
    portal = options_of(portal_chars, "portal")
    multiple = multiple_names(chars)
    found = []
    for item in fragments:
        if item["form"] == "words":
            fragment_words = words(item["text"])
            found += chosen(
                explain(fragment_words, ktru, prior, shared),
                item,
                fragment_words,
                "value",
                multiple,
            )
            # Слова товара не подсказывают характеристику портала: «Вид стола: Письменный».
            told = [word for word in fragment_words if word["stem"] not in product]
            found += chosen(explain(told, portal, prior, shared), item, told, "value", multiple)
    return found


def searched_text(char: Characteristic, value: str) -> str:
    """«Да» ищем по названию: «из натуральной кожи» значит «Натуральная кожа: Да»."""
    if value == "Да":
        return char["name"]
    return value


def number_values(
    fragments: list[Fragment],
    chars: list[Characteristic],
    portal_chars: list[Characteristic],
    allowed: dict[str, list[Value]],
    product: set[str],
) -> list[FoundChar]:
    """Числа в единице КТРУ: «2,2 см» → Толщина столешницы 22 мм, «высота 75» → 75 см."""
    numeric = numeric_chars(chars)
    portal_numeric = numeric_chars(portal_chars)
    found: list[FoundChar] = []
    for item in fragments:
        if item["form"] != "number" or item["number"] is None:
            continue
        name_stems = stems(item["name"] or "")
        char = owner(numeric, item["unit"], name_stems, guess=True)
        source: Source = "ktru"
        if char is None:
            # В названиях портала много мусора, поэтому название должно быть в тексте целиком.
            told = name_stems - product
            named_here = [c for c in portal_numeric if stems(c["name"]) <= told]
            char = owner(named_here, item["unit"], told, guess=False)
            source = "portal"
        if char is None:
            continue
        values = allowed.get(char["name"], [])
        char_found = number_char(
            char, source, "number", item["number"], item["unit"], values, item["text"]
        )
        char_found["start"], char_found["end"] = item["start"], item["end"]
        found.append(char_found)
    return found


def numeric_chars(chars: list[Characteristic]) -> list[Characteristic]:
    numeric = {}
    for char in chars:
        if char["type"] == "number" and "unit" in char:
            numeric[char["name"]] = char
    return list(numeric.values())


def number_char(
    char: Characteristic,
    source: Source,
    rule: Rule,
    number: float,
    code: str | None,
    allowed: list[Value],
    fragment: str,
) -> FoundChar:
    unit: Unit | None = char["unit"] if "unit" in char else None
    value = number
    if unit and code:
        value = convert(number, code, unit["code"])
    item = found_char(char, tidy(value), source, rule, fragment, unit=unit)
    if unit and code and code != unit["code"]:
        mark(item, "unit")
    check_range(item, allowed)
    return item


def check_range(item: FoundChar, allowed: list[Value]) -> None:
    """Число вне коридора КТРУ: правим, если сдвиг на порядок его объясняет, иначе помечаем."""
    corridor = bounds(allowed)
    value = item["value"]
    if corridor is None or isinstance(value, str) or within(value, corridor):
        return
    for shift in SHIFTS:
        if within(value * shift, corridor):
            item["value"] = tidy(value * shift)
            mark(item, "wrong_unit")
            return
    low, high = corridor
    mark(item, "out_of_range", low=show(low), high=show(high))


def within(value: float, corridor: tuple[float, float]) -> bool:
    return corridor[0] <= value <= corridor[1]


def owner(
    chars: list[Characteristic], code: str | None, name_stems: set[str], guess: bool
) -> Characteristic | None:
    """Характеристика той же величины, чьё название ближе к словам перед числом. Угадывать
    без подсказки в тексте можно, только если выбора нет, единица названа и guess разрешён."""
    best = None
    best_hits = 0
    same_dimension = 0
    for char in chars:
        if code is not None and scale(char["unit"]["code"])[0] != scale(code)[0]:
            continue
        same_dimension += 1
        hits = len(stems(char["name"]) & name_stems)
        if best is None or hits > best_hits:
            best = char
            best_hits = hits
    if best_hits == 0 and (not guess or same_dimension > 1 or code is None):
        return None
    return best
