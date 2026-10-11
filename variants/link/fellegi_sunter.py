"""Веса привязки числа это log(m/u) признаков Феллеги–Сантера, выученные EM без разметки по листу 1.

Вывод: dev 474/117/83 → 522/84/68, цена 0.420 → 0.326, лучше 51, хуже 2. У number_likelihood
с ручными весами 527/79/68 и 0.319: расхождения только 5 «верно → отказ», где у числа нет
читаемых слов («67*22 см», «рот 188 см»); по листу 1 такое число редко чьё, и вариант честно
отказывается. Выученные веса повторили ручной порядок и почти те же отношения: число точно 0,
внутри −1.0 (руками −0.7), сдвиг −3.1 (−2.3), мимо −3.6 (−4.6); единица другая −3.4 (−4.6).
EM по всем парам без ограничения «один владелец» вырождается: π → 0.996."""

from collections import Counter
from difflib import get_close_matches
from functools import cache
from math import exp, log
from typing import Literal, TypedDict

from contracts.pipeline import Candidate, FoundChar, Fragment, Rule
from contracts.ref import Characteristic, Portal, PortalChar, Position, Range, Unit, Value
from pipeline.explain import mark
from pipeline.extract import NUMBER, extract
from pipeline.text import Word, correct, stems, vocabulary, words
from pipeline.values import UNIT_ALIASES, bounds, convert, matches, same, scale, show, tidy
from scripts.dataloader import load_portal
from scripts.products import products

# Перепутанная единица чаще всего сдвигает число на порядок: см вместо мм, м вместо см.
SHIFTS = (10, 0.1, 100, 0.01, 1000, 0.001)
SHOWN_OPTIONS = 6
FEATURES = 3
TOLERANCE = 1e-6

type Pattern = tuple[float, str, str]
type Weights = dict[tuple[int, str], float]
type Mention = tuple[tuple[Pattern, int], ...]
type Source = Literal["ktru", "portal"]


class Match(TypedDict):
    char: FoundChar
    value_words: int
    name_words: int


def link(
    fragments: list[Fragment],
    candidates: list[Candidate],
    section: str | None,
    ref: list[Position],
    portal: Portal,
) -> list[FoundChar]:
    by_code = {p["code"]: p for p in ref}
    chars = []
    for candidate in candidates:
        chars.extend(by_code[candidate["code"]]["characteristics"])
    allowed = allowed_values(chars)
    portal_chars = [as_ktru(char) for char in portal.get(section or "", [])]
    product = stems(" ".join(c["name"] for c in candidates))

    pairs = told_pairs(fragments, chars, portal_chars, allowed, product)
    values = told_values(fragments, chars, portal_chars, product)
    numbers = number_values(fragments, chars, portal_chars, allowed, product)
    found = choose(pairs, values, numbers)
    # Обязательность у соседних кодов разная: обязательна, если обязательна хоть у одного.
    required = {char["name"] for char in chars if char["required"]}
    for char in found:
        char["required"] = char["source"] == "ktru" and char["name"] in required
    return found


def choose(
    pairs: list[FoundChar], values: list[Match], numbers: list[FoundChar]
) -> list[FoundChar]:
    """Единственное место, где решается, какое значение победит. Явная пара сильнее значения
    без названия, оно сильнее числа. Внутри правила спор решается молча, между правилами
    разные значения одной характеристики дают правку «два значения»."""
    found = last_per_name(pairs) + best_per_name(best_per_span(values)) + numbers
    return without_conflicts(found)


def last_per_name(found: list[FoundChar]) -> list[FoundChar]:
    """Человек дописывает исправление в конец, поэтому из явных значений верно последнее."""
    last = {}
    for char in found:
        last[char["name"]] = char
    return list(last.values())


def best_per_span(matches: list[Match]) -> list[Match]:
    """Кусок текста одной характеристике: «ЛДСП» у столешницы, если она есть в тексте."""
    by_span: dict[tuple[int, int], list[Match]] = {}
    for match in matches:
        span = (match["char"]["start"], match["char"]["end"])
        by_span.setdefault(span, []).append(match)
    return [max(group, key=strength) for group in by_span.values()]


def strength(match: Match) -> tuple[bool, int, int, bool, int]:
    """КТРУ побеждает портал, затем полнота значения, слова названия в тексте, обязательная,
    затем короче: из «Красный» и «3022 красная» верно первое."""
    char = match["char"]
    return (
        char["source"] == "ktru",
        match["value_words"],
        match["name_words"],
        char["required"],
        -len(show(char["value"])),
    )


def best_per_name(matches: list[Match]) -> list[FoundChar]:
    """Из двух значений одной характеристики берём значение КТРУ, затем более полное."""
    best: dict[str, Match] = {}
    for match in matches:
        kept = best.get(match["char"]["name"])
        if kept is None or fuller(match) > fuller(kept):
            best[match["char"]["name"]] = match
    return [match["char"] for match in best.values()]


def fuller(match: Match) -> tuple[bool, int]:
    return match["char"]["source"] == "ktru", match["value_words"]


def without_conflicts(found: list[FoundChar]) -> list[FoundChar]:
    kept: dict[str, FoundChar] = {}
    for char in found:
        first = kept.get(char["name"])
        if first is None:
            kept[char["name"]] = char
        elif not same(first["value"], char["value"]):
            mark(first, "conflict", other=char["fragment"])
    return list(kept.values())


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


def told_pairs(
    fragments: list[Fragment],
    chars: list[Characteristic],
    portal_chars: list[Characteristic],
    allowed: dict[str, list[Value]],
    product: set[str],
) -> list[FoundChar]:
    found = []
    for item in fragments:
        if item["form"] == "pair":
            char = pair_char(item, chars, portal_chars, allowed, product)
            if char:
                found.append(char)
    return found


def pair_char(
    item: Fragment,
    chars: list[Characteristic],
    portal_chars: list[Characteristic],
    allowed: dict[str, list[Value]],
    product: set[str],
) -> FoundChar | None:
    offset = name_offset(item["name"] or "", product)
    if offset is None:
        return None
    name = (item["name"] or "")[offset:]
    start = item["start"] + offset
    fragment = item["text"][offset:]
    char = named(chars, name)
    source: Source = "ktru"
    if char is None:
        char = named(portal_chars, name)
        source = "portal"
    if char is None:
        return new_char(name, item["value"], fragment, start, item["end"])
    values = allowed.get(char["name"], []) if source == "ktru" else char["values"]
    if char["type"] == "number":
        if item["number"] is None:
            return None
        found = number_char(char, source, "pair", item["number"], item["unit"], values, fragment)
    else:
        found = text_pair(char, source, item["value"], fragment, values)
    found["start"], found["end"] = start, item["end"]
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
    char: Characteristic, source: Source, raw: str, fragment: str, values: list[Value]
) -> FoundChar:
    """Значение из списка, ближайшее при опечатке, иначе как есть. Списки портала собраны
    из того, что вводили поставщики, поэтому «нет в списке» говорим только про КТРУ."""
    options = {v.casefold(): v for v in values if isinstance(v, str)}
    item = found_char(char, raw, source, "pair", fragment)
    if raw.casefold() in options:
        item["value"] = options[raw.casefold()]
        return item
    close = get_close_matches(raw.casefold(), options, n=1, cutoff=0.75)
    if close:
        item["value"] = options[close[0]]
        mark(item, "typo")
        return item
    if source == "ktru":
        shown = ", ".join(sorted(set(options.values()))[:SHOWN_OPTIONS])
        mark(item, "not_in_list", options=shown)
    return item


def told_values(
    fragments: list[Fragment],
    chars: list[Characteristic],
    portal_chars: list[Characteristic],
    product: set[str],
) -> list[Match]:
    """Значения без названия: «деревянный» → Тип каркаса: Деревянный."""
    texts = []
    for char in chars + portal_chars:
        for value in char["values"]:
            if isinstance(value, str):
                texts.append(searched_text(char, value))
    vocab = vocabulary(texts)

    matches = []
    for item in fragments:
        if item["form"] == "words":
            fragment_words = correct(words(item["text"]), vocab)
            matches += find_matches(item, fragment_words, chars, "ktru")
            # Слова товара не подсказывают характеристику портала: «Вид стола: Письменный».
            told = [word for word in fragment_words if word["stem"] not in product]
            matches += find_matches(item, told, portal_chars, "portal")
    return matches


def find_matches(
    item: Fragment, fragment_words: list[Word], chars: list[Characteristic], source: Source
) -> list[Match]:
    """Все значения, каждое слово которых есть в куске, без выбора. Значение портала берём,
    только если рядом названа и сама характеристика: в списках портала много мусора."""
    text_stems = {word["stem"] for word in fragment_words}
    matches: list[Match] = []
    for char in chars:
        name_words = len(stems(char["name"]) & text_stems)
        if source == "portal" and not name_words:
            continue
        for value in char["values"]:
            if not isinstance(value, str) or value == "Нет":
                continue
            wanted = stems(searched_text(char, value))
            # «Размер: Размер 9, l» совпадает с текстом одним названием, это не значение.
            if source == "portal" and value != "Да" and wanted <= stems(char["name"]):
                continue
            hits = [word for word in fragment_words if word["stem"] in wanted]
            if not wanted or {word["stem"] for word in hits} != wanted:
                continue
            start = min(word["start"] for word in hits)
            end = max(word["end"] for word in hits)
            fragment = item["text"][start:end]
            found = found_char(
                char, value, source, "value", fragment, item["start"] + start, item["start"] + end
            )
            fixes = [word["fix"] for word in hits if word["fix"]]
            if fixes:
                mark(found, fixes[0])
            matches.append({"char": found, "value_words": len(wanted), "name_words": name_words})
    return matches


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
    """Владелец числа один из K кандидатов или никто. P(владелец c) ∝ π/K · m(γ_c)/u(γ_c),
    P(никто) ∝ 1 − π: веса m/u и доля чисел с владельцем π выучены EM. Отвечаем, только если
    владелец вероятнее всех остальных вместе с «число ни при чём»."""
    weight, owned = weights()
    pool = [(c, "ktru") for c in numbered(chars)]
    # В названиях портала много мусора, поэтому его характеристика должна быть названа целиком.
    pool += [(c, "portal") for c in numbered(portal_chars)]
    vocab = vocabulary(c["name"] for c, _ in pool)
    numbers = [f for f in fragments if f["form"] == "number" and f["number"] is not None]
    contexts = number_contexts(numbers, fragments, vocab)
    chosen: list[tuple[Fragment, Characteristic, Source]] = []
    for item, context in zip(numbers, contexts, strict=True):
        scored = []
        for char, source in pool:
            values = allowed.get(char["name"], []) if source == "ktru" else char["values"]
            share = told_share(char, context, product)
            if source == "portal" and share < 1:
                continue
            ratio = exp(log_ratio(weight, pattern(char, item, share, values)))
            scored.append((ratio, share, char, source))
        if not scored:
            continue
        prior = owned / len(scored)
        total = 1 - owned + prior * sum(s[0] for s in scored)
        ratio, _, char, source = max(scored, key=lambda s: s[:2])
        if prior * ratio / total > 0.5:
            chosen.append((item, char, source))
    return number_chars(chosen, allowed)


@cache
def weights() -> tuple[Weights, float]:
    """Вес уровня признака log(m/u) и доля чисел с владельцем π. Учимся без разметки на товарах
    листа 1: их пары склеены в текст, кандидаты это числовые характеристики раздела листа 2."""
    # Узоров сравнения десятки, поэтому число это счётчик узоров, а числа с одинаковыми
    # счётчиками считаются один раз, как в fastLink.
    groups = Counter(tuple(sorted(Counter(m).items())) for m in training_mentions())
    return em(groups)


def training_mentions() -> list[list[Pattern]]:
    portal = load_portal()
    found = [p for p in products({}, build=False) if not p["test"] and p["section"]]
    seen: dict[tuple[str, str], Counter[float]] = {}
    for product in found:
        for name, value in product["pairs"]:
            number = NUMBER.search(value)
            if number:
                key = (product["section"] or "", name)
                seen.setdefault(key, Counter())[float(number["number"].replace(",", "."))] += 1
    mentions = []
    for product in found:
        section = product["section"] or ""
        chars = numbered([as_ktru(c) for c in portal.get(section, [])])
        if not chars:
            continue
        mine = {name: value for name, value in product["pairs"]}
        # Название товара это свободный текст: в нём числа без названия характеристики.
        pairs = [f"{name} {value}" for name, value in product["pairs"]]
        text = ", ".join([product["name"], *pairs])
        fragments = extract(text)
        numbers = [f for f in fragments if f["form"] == "number" and f["number"] is not None]
        vocab = vocabulary(c["name"] for c in chars)
        contexts = number_contexts(numbers, fragments, vocab)
        # Аналог названий кандидатов при привязке: их слова не различают характеристики.
        section_stems = stems(section)
        for item, context in zip(numbers, contexts, strict=True):
            patterns = []
            for char in chars:
                values = char["values"]
                if char["type"] == "number":
                    others = seen.get((section, char["name"]), Counter()).copy()
                    own = NUMBER.search(mine.get(char["name"], ""))
                    # Своё значение не в счёт: иначе своя пара всегда «точно».
                    if own:
                        others[float(own["number"].replace(",", "."))] -= 1
                    values = [v for v, times in others.items() if times > 0]
                share = told_share(char, context, section_stems)
                patterns.append(pattern(char, item, share, values))
            mentions.append(patterns)
    return mentions


def em(groups: Counter[Mention]) -> tuple[Weights, float]:
    """EM с ограничением «у числа не больше одного владельца»: ответственность пары это та же
    апостериорная вероятность, что и при привязке, поэтому пары одного числа не считаются
    независимыми. Частичное совпадение слов не свой уровень, а смесь весов «все» и «ни одного»
    (Винклер), и m/u слов учатся только на полных совпадениях и промахах."""
    levels = [{level(p, f) for g in groups for p, _ in g} - {None} for f in range(FEATURES)]
    agree = ("all", "exact", "same")
    m = [{lv: 0.9 if lv == agree[f] else 0.1 for lv in levels[f]} for f in range(FEATURES)]
    u = [dict.fromkeys(levels[f], 1.0) for f in range(FEATURES)]
    owned = 0.5
    mentions = sum(groups.values())
    while True:
        weight = {(f, lv): log(m[f][lv] / u[f][lv]) for f in range(FEATURES) for lv in levels[f]}
        m_count = [dict.fromkeys(levels[f], 1.0) for f in range(FEATURES)]
        u_count = [dict.fromkeys(levels[f], 1.0) for f in range(FEATURES)]
        owners = 0.0
        for patterns, times in groups.items():
            ratios = [exp(log_ratio(weight, p)) for p, _ in patterns]
            prior = owned / sum(n for _, n in patterns)
            total = (
                1 - owned + prior * sum(r * n for r, (_, n) in zip(ratios, patterns, strict=True))
            )
            for (p, n), ratio in zip(patterns, ratios, strict=True):
                share = prior * ratio / total
                owners += times * n * share
                for f in range(FEATURES):
                    lv = level(p, f)
                    if lv is not None:
                        m_count[f][lv] += times * n * share
                        u_count[f][lv] += times * n * (1 - share)
        new_m = [normalized(c) for c in m_count]
        new_u = [normalized(c) for c in u_count]
        change = max(
            abs(new_m[f][lv] - m[f][lv]) + abs(new_u[f][lv] - u[f][lv])
            for f in range(FEATURES)
            for lv in levels[f]
        )
        m, u, owned = new_m, new_u, owners / mentions
        if change < TOLERANCE:
            break
    weight = {(f, lv): log(m[f][lv] / u[f][lv]) for f in range(FEATURES) for lv in levels[f]}
    return weight, owned


def level(p: Pattern, feature: int) -> str | None:
    if feature:
        return p[feature]
    return {1.0: "all", 0.0: "none"}.get(p[0])


def log_ratio(weight: Weights, p: Pattern) -> float:
    share, number, unit = p
    told = weight[0, "none"] + share * (weight[0, "all"] - weight[0, "none"])
    return told + weight[1, number] + weight[2, unit]


def normalized(counts: dict[str, float]) -> dict[str, float]:
    total = sum(counts.values())
    return {key: value / total for key, value in counts.items()}


def numbered(chars: list[Characteristic]) -> list[Characteristic]:
    """Числовые характеристики и текстовые, среди значений которых есть числа: «размер 52»."""
    found = {}
    for char in chars:
        if char["type"] == "number" or any(as_number(v) is not None for v in char["values"]):
            found[char["name"]] = char
    return list(found.values())


def as_number(value: Value) -> float | None:
    if isinstance(value, str):
        try:
            return float(value.replace(",", "."))
        except ValueError:
            return None
    return None if isinstance(value, dict) else float(value)


def number_contexts(
    numbers: list[Fragment], fragments: list[Fragment], vocab: dict[str, str]
) -> list[set[str]]:
    """Слова слева до прошлого числа и справа до следующего: «4 секций», «размер 52».
    Между числами диапазона слов нет, такие числа делят слова соседа: «длина 250-300»."""
    lefts, rights = [], []
    for index, item in enumerate(numbers):
        clause = enclosing(item, fragments)
        before = (
            numbers[index - 1]["end"] if index and same_clause(numbers[index - 1], clause) else None
        )
        after = (
            numbers[index + 1]["start"]
            if index + 1 < len(numbers) and same_clause(numbers[index + 1], clause)
            else None
        )
        if clause is None:
            lefts.append(set(w["stem"] for w in correct(words(item["name"] or ""), vocab)))
            rights.append(set())
            continue
        offset = clause["start"]
        left = clause["text"][(before or offset) - offset : item["start"] - offset]
        right = clause["text"][item["end"] - offset : (after or clause["end"]) - offset]
        lefts.append({w["stem"] for w in correct(words(left), vocab)})
        rights.append({w["stem"] for w in correct(words(right), vocab)})
    for index in range(1, len(numbers)):
        if not lefts[index] and same_span(numbers[index - 1], numbers[index], fragments):
            lefts[index] = lefts[index - 1]
    for index in range(len(numbers) - 2, -1, -1):
        if not rights[index] and same_span(numbers[index], numbers[index + 1], fragments):
            rights[index] = rights[index + 1]
    return [left | right for left, right in zip(lefts, rights, strict=True)]


def enclosing(item: Fragment, fragments: list[Fragment]) -> Fragment | None:
    for clause in fragments:
        if clause["form"] == "words" and clause["start"] <= item["start"] < clause["end"]:
            return clause
    return None


def same_clause(item: Fragment, clause: Fragment | None) -> bool:
    return clause is not None and clause["start"] <= item["start"] < clause["end"]


def same_span(first: Fragment, second: Fragment, fragments: list[Fragment]) -> bool:
    clause = enclosing(first, fragments)
    if clause is None or not same_clause(second, clause):
        return False
    between = clause["text"][first["end"] - clause["start"] : second["start"] - clause["start"]]
    return not words(between)


def told_share(char: Characteristic, context: set[str], product: set[str]) -> float:
    """Общие слова от всех слов названия и окружения: «длина полки» ближе к «Длина полки»,
    чем к «Длина». Слова товара не различают его характеристики."""
    name = stems(char["name"])
    name = name - product or name
    context = context - product
    return len(name & context) / len(name | context) if name else 0


def pattern(char: Characteristic, item: Fragment, share: float, values: list[Value]) -> Pattern:
    """Вектор сравнения Феллеги–Сантера: слова рядом, число, единица."""
    return round(share, 3), number_level(char, item, values), unit_level(char, item["unit"])


def number_level(char: Characteristic, item: Fragment, values: list[Value]) -> str:
    """Точное значение или диапазон КТРУ, внутри общего коридора, после сдвига на порядок, вне
    всего, сравнивать не с чем."""
    number = item["number"]
    assert number is not None
    unit = char.get("unit")
    if unit and item["unit"] and scale(unit["code"])[0] == scale(item["unit"])[0]:
        number = convert(number, item["unit"], unit["code"])
    if char["type"] != "number":
        listed = [as_number(v) for v in values if isinstance(v, str)]
        return "exact" if number in listed else "none"
    if any(matches(number, v) for v in values):
        return "exact"
    corridor = bounds(values)
    if corridor is None:
        return "unknown"
    if within(number, corridor):
        return "inside"
    if any(within(number * shift, corridor) for shift in SHIFTS):
        return "shifted"
    return "none"


def unit_level(char: Characteristic, code: str | None) -> str:
    if code is None:
        return "absent"
    unit = char.get("unit")
    if unit is None:
        return "nowhere"
    return "same" if scale(unit["code"])[0] == scale(code)[0] else "other"


def number_chars(
    chosen: list[tuple[Fragment, Characteristic, Source]], allowed: dict[str, list[Value]]
) -> list[FoundChar]:
    """Два числа подряд у одной характеристики, ровно диапазон КТРУ: «длина 250-300»."""
    found: list[FoundChar] = []
    index = 0
    while index < len(chosen):
        item, char, source = chosen[index]
        values = allowed.get(char["name"], []) if source == "ktru" else char["values"]
        if index + 1 < len(chosen) and chosen[index + 1][1]["name"] == char["name"]:
            second = chosen[index + 1][0]
            span = range_value(char, item, second, values)
            if span is not None:
                char_found = found_char(
                    char,
                    show(span),
                    source,
                    "number",
                    "",
                    item["start"],
                    second["end"],
                    char.get("unit"),
                )
                found.append(char_found)
                index += 2
                continue
        if char["type"] == "number":
            char_found = number_char(
                char, source, "number", item["number"] or 0, item["unit"], values, item["text"]
            )
        else:
            char_found = listed_char(char, source, item, values)
        char_found["start"], char_found["end"] = item["start"], item["end"]
        found.append(char_found)
        index += 1
    return found


def range_value(
    char: Characteristic, first: Fragment, second: Fragment, values: list[Value]
) -> Range | None:
    unit = char.get("unit")
    code = second["unit"] or first["unit"]
    low, high = first["number"] or 0, second["number"] or 0
    if unit and code:
        low, high = convert(low, code, unit["code"]), convert(high, code, unit["code"])
    for value in values:
        if isinstance(value, dict) and value.get("min") == low and value.get("max") == high:
            return value
    return None


def listed_char(
    char: Characteristic, source: Source, item: Fragment, values: list[Value]
) -> FoundChar:
    options = {as_number(v): v for v in values if isinstance(v, str)}
    number = item["number"] or 0
    found = found_char(char, options.get(number, tidy(number)), source, "number", item["text"])
    if number not in options and source == "ktru":
        shown = ", ".join(sorted({str(v) for v in options.values()})[:SHOWN_OPTIONS])
        mark(found, "not_in_list", options=shown)
    return found


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
