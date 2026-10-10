"""Бейзлайн: правила с порядком приоритетов привязывают куски к характеристикам КТРУ и портала."""

from difflib import get_close_matches
from typing import Literal, TypedDict

from contracts.pipeline import Candidate, FoundChar, Fragment, Rule
from contracts.ref import Characteristic, Portal, PortalChar, Position, Unit, Value
from pipeline.explain import mark
from pipeline.text import Word, correct, stems, vocabulary, words
from pipeline.values import UNIT_ALIASES, bounds, convert, same, scale, show, tidy

# Перепутанная единица чаще всего сдвигает число на порядок: см вместо мм, м вместо см.
SHIFTS = (10, 0.1, 100, 0.01, 1000, 0.001)
SHOWN_OPTIONS = 6

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
