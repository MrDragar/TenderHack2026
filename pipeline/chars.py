import re
from difflib import get_close_matches
from typing import TypedDict

from contracts.pipeline import Candidates, FixKind, FoundChar
from contracts.ref import Characteristic, Portal, Position, Value
from pipeline.text import PAIR, Word, correct, stems, vocabulary, words
from pipeline.values import bounds, show

UNIT_ALIASES = {
    "мм": "003",
    "см": "004",
    "м": "006",
    "км": "008",
    "дюйм": "039",
    "г": "163",
    "гр": "163",
    "кг": "166",
    "шт": "796",
    "вт": "212",
    "в": "222",
    "ач": "263",
    "об/мин": "331",
}
# Единицы одной величины переводятся друг в друга через множитель к базовой.
SCALE = {
    "003": ("length", 1),
    "004": ("length", 10),
    "006": ("length", 1000),
    "008": ("length", 1_000_000),
    "039": ("length", 25.4),
    "163": ("mass", 1),
    "166": ("mass", 1000),
}
NUMBER = re.compile(
    r"(?P<number>\d+(?:[.,]\d+)?)(?:\s*(?P<unit>"
    + "|".join(sorted(UNIT_ALIASES, key=len, reverse=True))
    + r")\b)?",
    re.IGNORECASE,
)
TENS = {
    "десять": 10,
    "двадцать": 20,
    "тридцать": 30,
    "сорок": 40,
    "пятьдесят": 50,
    "шестьдесят": 60,
    "семьдесят": 70,
    "восемьдесят": 80,
    "девяносто": 90,
}
# «Метр десять» так говорят о росте и высоте: метр и сантиметры словом.
SPOKEN = re.compile(r"\bметр[а]?\s+(?P<tens>" + "|".join(TENS) + r")\b", re.IGNORECASE)
# Перепутанная единица чаще всего сдвигает число на порядок: см вместо мм, м вместо см.
SHIFTS = (10, 0.1, 100, 0.01, 1000, 0.001)
SHOWN_OPTIONS = 6


class Match(TypedDict):
    char: str
    value: str
    start: int
    end: int
    value_words: int
    name_words: int
    required: bool
    fix: FixKind | None


def extract(
    text: str, candidates: Candidates, ref: list[Position], portal: Portal
) -> list[FoundChar]:
    by_code = {p["code"]: p for p in ref}
    chars = []
    for candidate in candidates["candidates"]:
        chars.extend(by_code[candidate["code"]]["characteristics"])
    allowed = allowed_values(chars)

    product = stems(" ".join(c["name"] for c in candidates["candidates"]))
    told = last_per_name(pair_values(text, chars, allowed, product))
    taken = [(c["start"], c["end"]) for c in told]
    found = told + text_values(text, chars, taken) + number_values(text, chars, allowed, taken)
    # Обязательность у соседних кодов разная: обязательна, если обязательна хоть у одного.
    required = {char["name"] for char in chars if char["required"]}
    for char in found:
        char["required"] = char["name"] in required
    return without_conflicts(found)


def allowed_values(chars: list[Characteristic]) -> dict[str, list[Value]]:
    """Значения характеристики по всем кандидатам: у соседних кодов списки разные."""
    allowed: dict[str, list[Value]] = {}
    for char in chars:
        allowed.setdefault(char["name"], []).extend(char["values"])
    return allowed


def pair_values(
    text: str, chars: list[Characteristic], allowed: dict[str, list[Value]], product: set[str]
) -> list[FoundChar]:
    """Явное «Тип каркаса: Деревянный»: так пишет человек, ответ на вопрос и правка таблицы."""
    found: list[FoundChar] = []
    for match in PAIR.finditer(text):
        start, end = name_start(text, match, product), match.end("value")
        if start is None:
            continue
        name, raw = text[start : match.end("name")].strip(), match["value"].strip()
        char = named(chars, name)
        if char is None:
            found.append(new_char(name, raw, text, start, end))
        elif char["type"] == "number":
            number = NUMBER.search(raw)
            if number is None:
                continue
            item = number_char(
                char, number_of(number), unit_code(number), allowed[char["name"]], text[start:end]
            )
            item["start"], item["end"] = start, end
            found.append(item)
        else:
            found.append(text_pair(char, raw, allowed[char["name"]], text, start, end))
    return found


def name_start(text: str, match: re.Match[str], product: set[str]) -> int | None:
    """«Джемпер рост: 170»: слова товара перед названием характеристики к нему не относятся."""
    for word in words(match["name"]):
        if word["stem"] not in product:
            return match.start("name") + word["start"]
    return None


def last_per_name(found: list[FoundChar]) -> list[FoundChar]:
    """Человек дописывает исправление в конец, поэтому из явных значений верно последнее."""
    last = {}
    for char in found:
        last[char["name"]] = char
    return list(last.values())


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


def new_char(name: str, raw: str, text: str, start: int, end: int) -> FoundChar:
    return {
        "name": name[:1].upper() + name[1:],
        "value": raw,
        "unit": None,
        "source": "new",
        "fragment": text[start:end],
        "start": start,
        "end": end,
        "required": False,
        "fix": None,
        "kind": None,
    }


def text_pair(
    char: Characteristic, raw: str, allowed: list[Value], text: str, start: int, end: int
) -> FoundChar:
    """Значение из списка КТРУ, ближайшее при опечатке, иначе как есть с замечанием."""
    options = {str(v).casefold(): str(v) for v in allowed if isinstance(v, str)}
    item: FoundChar = {
        "name": char["name"],
        "value": raw,
        "unit": None,
        "source": "ktru",
        "fragment": text[start:end],
        "start": start,
        "end": end,
        "required": char["required"],
        "fix": None,
        "kind": None,
    }
    if raw.casefold() in options:
        item["value"] = options[raw.casefold()]
        return item
    close = get_close_matches(raw.casefold(), options, n=1, cutoff=0.75)
    if close:
        item["value"] = options[close[0]]
        item["fix"] = f"опечатка, в КТРУ пишется «{options[close[0]]}»"
        item["kind"] = "typo"
        return item
    shown = ", ".join(sorted(set(options.values()))[:SHOWN_OPTIONS])
    item["fix"] = f"такого значения нет в КТРУ, допустимо: {shown}"
    item["kind"] = "not_in_list"
    return item


def text_values(
    text: str, chars: list[Characteristic], taken: list[tuple[int, int]]
) -> list[FoundChar]:
    """Текстовые значения КТРУ из текста: «деревянный» → Тип каркаса: Деревянный."""
    best: dict[str, Match] = {}
    for match in best_per_fragment(find_matches(text, chars, taken)):
        kept = best.get(match["char"])
        # Из двух значений одной характеристики берём более полное: «Металл хромированный».
        if kept is None or match["value_words"] > kept["value_words"]:
            best[match["char"]] = match

    found: list[FoundChar] = []
    for match in best.values():
        fragment = text[match["start"] : match["end"]]
        found.append(
            {
                "name": match["char"],
                "value": match["value"],
                "unit": None,
                "source": "ktru",
                "fragment": fragment,
                "start": match["start"],
                "end": match["end"],
                "required": match["required"],
                "fix": word_fix(match["fix"], match["value"]),
                "kind": match["fix"],
            }
        )
    return found


def word_fix(kind: FixKind | None, value: str) -> str | None:
    if kind == "typo":
        return f"опечатка, в КТРУ пишется «{value}»"
    if kind == "layout":
        return f"текст набран не в той раскладке или латиницей, это «{value}»"
    return None


def find_matches(
    text: str, chars: list[Characteristic], taken: list[tuple[int, int]]
) -> list[Match]:
    """Все значения кандидатов, каждое слово которых есть в тексте, без выбора."""
    texts = []
    for char in chars:
        for value in char["values"]:
            if isinstance(value, str):
                texts.append(searched_text(char, value))
    text_words = []
    for word in correct(words(text), vocabulary(texts)):
        if not inside(word, taken):
            text_words.append(word)
    text_stems = {word["stem"] for word in text_words}
    matches: list[Match] = []
    for char in chars:
        name_words = len(stems(char["name"]) & text_stems)
        for value in char["values"]:
            if not isinstance(value, str) or value == "Нет":
                continue
            wanted = stems(searched_text(char, value))
            hits = [word for word in text_words if word["stem"] in wanted]
            if not wanted or {word["stem"] for word in hits} != wanted:
                continue
            fixes = [word["fix"] for word in hits if word["fix"]]
            matches.append(
                {
                    "char": char["name"],
                    "value": value,
                    "start": min(word["start"] for word in hits),
                    "end": max(word["end"] for word in hits),
                    "value_words": len(wanted),
                    "name_words": name_words,
                    "required": char["required"],
                    "fix": fixes[0] if fixes else None,
                }
            )
    return matches


def inside(word: Word, taken: list[tuple[int, int]]) -> bool:
    return any(start <= word["start"] < end for start, end in taken)


def searched_text(char: Characteristic, value: str) -> str:
    """«Да» ищем по названию: «из натуральной кожи» значит «Натуральная кожа: Да»."""
    if value == "Да":
        return char["name"]
    return value


def best_per_fragment(matches: list[Match]) -> list[Match]:
    """Кусок текста одной характеристике: «ЛДСП» у столешницы, если она есть в тексте."""
    by_fragment: dict[tuple[int, int], list[Match]] = {}
    for match in matches:
        by_fragment.setdefault((match["start"], match["end"]), []).append(match)

    return [max(group, key=strength) for group in by_fragment.values()]


def strength(match: Match) -> tuple[int, int, bool]:
    """Полнота значения, слова названия характеристики в тексте, затем обязательная."""
    return match["value_words"], match["name_words"], match["required"]


def number_values(
    text: str,
    chars: list[Characteristic],
    allowed: dict[str, list[Value]],
    taken: list[tuple[int, int]],
) -> list[FoundChar]:
    """Числа в единице КТРУ: «2,2 см» → Толщина столешницы 22 мм, «высота 75» → 75 см."""
    numeric = {}
    for char in chars:
        if char["type"] == "number" and "unit" in char:
            numeric[char["name"]] = char

    spoken = [(m, 1 + TENS[m["tens"].lower()] / 100, "006") for m in SPOKEN.finditer(text)]
    taken = taken + [(m.start(), m.end()) for m, _, _ in spoken]
    written = []
    for match in NUMBER.finditer(text):
        if not any(start <= match.start() < end for start, end in taken):
            written.append((match, number_of(match), unit_code(match)))

    found: list[FoundChar] = []
    for match, number, code in spoken + written:
        # Какой характеристике принадлежит число, решают слова перед ним до запятой.
        clause = re.split(r"[,;\n]", text[: match.start()])[-1]
        char = owner(list(numeric.values()), code, stems(clause))
        if char is None:
            continue
        item = number_char(char, number, code, allowed[char["name"]], match[0])
        item["start"], item["end"] = match.start(), match.end()
        found.append(item)
    return found


def number_of(match: re.Match[str]) -> float:
    return float(match["number"].replace(",", "."))


def unit_code(match: re.Match[str]) -> str | None:
    if match["unit"] is None:
        return None
    return UNIT_ALIASES[match["unit"].lower()]


def number_char(
    char: Characteristic,
    number: float,
    code: str | None,
    allowed: list[Value],
    fragment: str,
) -> FoundChar:
    unit = char["unit"] if "unit" in char else None
    value = number
    if unit and code:
        value = convert(number, code, unit["code"])
    item: FoundChar = {
        "name": char["name"],
        "value": tidy(value),
        "unit": unit,
        "source": "ktru",
        "fragment": fragment,
        "start": 0,
        "end": 0,
        "required": char["required"],
        "fix": None,
        "kind": None,
    }
    if unit and code and code != unit["code"]:
        item["fix"] = f"в КТРУ единица {unit['name'].lower()}, перевели"
        item["kind"] = "unit"
    check_range(item, allowed)
    return item


def check_range(item: FoundChar, allowed: list[Value]) -> None:
    """Число вне коридора КТРУ: правим, если сдвиг на порядок его объясняет, иначе помечаем."""
    corridor = bounds(allowed)
    value = item["value"]
    if corridor is None or isinstance(value, str) or within(value, corridor):
        return
    unit = item["unit"]["name"].lower() if item["unit"] else ""
    for shift in SHIFTS:
        if within(value * shift, corridor):
            item["value"] = tidy(value * shift)
            item["fix"] = (
                f"«{item['fragment']}» не бывает у такого товара, "
                f"похоже, перепутана единица: {show(item['value'])} {unit}"
            )
            item["kind"] = "wrong_unit"
            return
    low, high = corridor
    item["fix"] = f"вне допустимого по КТРУ: от {show(low)} до {show(high)} {unit}"
    item["kind"] = "out_of_range"


def within(value: float, corridor: tuple[float, float]) -> bool:
    return corridor[0] <= value <= corridor[1]


def tidy(value: float) -> int | float:
    return int(value) if float(value).is_integer() else round(value, 3)


def without_conflicts(found: list[FoundChar]) -> list[FoundChar]:
    """Два разных значения одной характеристики: оставляем первое и говорим о втором."""
    kept: dict[str, FoundChar] = {}
    for char in found:
        first = kept.get(char["name"])
        if first is None:
            kept[char["name"]] = char
        elif show(first["value"]).casefold() != show(char["value"]).casefold():
            first["fix"] = f"в тексте два значения: «{first['fragment']}» и «{char['fragment']}»"
            first["kind"] = "conflict"
    return list(kept.values())


def owner(
    chars: list[Characteristic], code: str | None, clause_stems: set[str]
) -> Characteristic | None:
    """Характеристика той же величины, чьё название ближе к словам перед числом."""
    best = None
    best_hits = 0
    same_dimension = 0
    for char in chars:
        if code is not None and scale(char["unit"]["code"])[0] != scale(code)[0]:
            continue
        same_dimension += 1
        hits = len(stems(char["name"]) & clause_stems)
        if best is None or hits > best_hits:
            best = char
            best_hits = hits
    # Без подсказки в тексте угадываем, только если выбора нет и единица названа.
    if best_hits == 0 and (same_dimension > 1 or code is None):
        return None
    return best


def convert(number: float, from_code: str, to_code: str) -> float:
    return number * scale(from_code)[1] / scale(to_code)[1]


def scale(unit_code: str) -> tuple[str, float]:
    return SCALE.get(unit_code, (unit_code, 1))
