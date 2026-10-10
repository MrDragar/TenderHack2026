import json
import re
import sys
import zlib
from difflib import SequenceMatcher
from random import Random
from typing import Literal, TypedDict

from contracts.eval import ExpectedChar
from contracts.pipeline import Candidate, FixKind, FoundChar, Fragment
from contracts.ref import Characteristic, Portal, Position, Value
from pipeline.link import SHIFTS
from pipeline.text import LATIN_KEYS, RUSSIAN_KEYS, stems
from pipeline.values import SCALE, bounds, show, tidy

NOISE = 0.3
MAX_CHARS = 4
FULL_NAME = 0.3
NAME_FIRST = 0.85
TO_LATIN = str.maketrans(RUSSIAN_KEYS, LATIN_KEYS)
TO_RUSSIAN = str.maketrans(LATIN_KEYS, RUSSIAN_KEYS)
TO_TWINS = str.maketrans("асеорху", "aceopxy")
# Как пишут единицы люди, а не как их понимает пайплайн: иначе тест проверит сам себя.
UNITS = {
    "003": "мм",
    "004": "см",
    "006": "м",
    "008": "км",
    "039": "дюйм",
    "050": "мм²",
    "163": "г",
    "166": "кг",
    "212": "Вт",
    "222": "В",
    "263": "Ач",
    "280": "°C",
    "331": "об/мин",
    "355": "мин",
    "598": "м³/ч",
    "796": "шт",
    "2355": "°",
}
NUMBER_WORDS = ("", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять")
VOWELS = {"а": "о", "о": "а", "е": "и", "и": "е", "я": "е"}
KEY_ROWS = ("йцукенгшщзхъ", "фывапролджэ", "ячсмитьбю")
# Доли видов опечаток примерно как в живых ошибках (SAGE, RUSpellRU): чаще безударная
# гласная и пропуск буквы, реже удвоение, перестановка и соседняя клавиша.
TYPOS = ("vowel",) * 3 + ("drop",) * 3 + ("double",) * 2 + ("swap", "key")
ADJECTIVE = re.compile(r"(ый|ий|ой|ая|яя|ое|ее|ые|ие)$")
LINKING = {"для", "из", "с", "со", "на", "от", "по", "в", "во", "без", "под", "и", "или"}
SYLLABLES = ("ka", "ro", "max", "tel", "vi", "for", "zen", "ul", "tra", "bo", "den", "gar")
COLORS = ("черный", "белый", "серый", "синий", "бежевый", "коричневый", "зеленый", "красный")
COUNTRIES = ("Россия", "Китай", "Беларусь", "Германия", "Турция")
KIND_TAGS: dict[FixKind, str] = {
    "typo": "опечатка в значении",
    "layout": "раскладка в значении",
    "not_in_list": "значения нет в списке",
    "unit": "другая единица",
    "wrong_unit": "перепутана единица",
    "out_of_range": "вне диапазона",
}
NAME_SPOILS = ("опечатка в названии", "раскладка в названии")
FLAG_WORDS = {"наличие", "возможность"}


class Generated(TypedDict):
    text: str
    code: str
    fragments: list[Fragment]
    chars: list[ExpectedChar]
    noisy: bool
    tags: list[str]


class Piece(TypedDict):
    text: str
    fragments: list[Fragment]
    chars: list[ExpectedChar]
    tags: list[str]


def held_out(key: str) -> bool:
    """Пятая часть позиций, разделов и товаров в test. Делим по ним, а не по текстам: иначе
    правило, подогнанное под позицию из dev, проверится на той же позиции."""
    return zlib.crc32(key.encode()) % 5 == 1


def generate(ref: list[Position], count: int, seed: int = 0, test: bool = False) -> list[Generated]:
    """Тексты с известным ответом. Шаблоны берём по очереди, а не позиции подряд: иначе
    спецодежда с сотнями кодов заслоняет остальное. Ответ в тексте не подсказан: название
    позиции чаще короткое, значения в живой форме, рядом слова не про КТРУ."""
    rng = Random(seed)
    by_code = {p["code"]: p for p in ref}
    names = template_names(ref)
    everywhere = allowed_values([p for p in ref if p["position_type"] == "final"])
    families: dict[str, list[Position]] = {}
    for position in ref:
        if position["position_type"] == "final" and held_out(position["code"]) == test:
            families.setdefault(template(position), []).append(position)
    order = sorted(families)
    rng.shuffle(order)
    total = min(count, sum(len(family) for family in families.values()))
    found: list[Generated] = []
    while len(found) < total:
        for key in order:
            left = families[key]
            if left and len(found) < total:
                position = left.pop(rng.randrange(len(left)))
                found.append(one(position, by_code, names, everywhere, rng))
    return found


def template(position: Position) -> str:
    return position["parent_code"] or position["code"]


def template_names(ref: list[Position]) -> dict[str, str]:
    return {template(p): p["name"] for p in ref if p["position_type"] == "final"}


def one(
    position: Position,
    by_code: dict[str, Position],
    names: dict[str, str],
    everywhere: dict[str, list[Value]],
    rng: Random,
) -> Generated:
    family = [by_code[c["code"]] for c in siblings(position, by_code)]
    allowed = allowed_values(family)
    usable = [c for c in position["characteristics"] if renderable(c)]
    chosen = rng.sample(usable, min(rng.randint(1, MAX_CHARS), len(usable)))
    pieces = []
    sizes = size_pair(chosen)
    if sizes and rng.random() < 0.5:
        pieces.append(sizes_piece(sizes[0], sizes[1], rng))
        chosen = [c for c in chosen if c not in sizes]
    for char in chosen:
        if char["type"] == "number":
            pieces.append(number_piece(char, allowed, family, rng))
        else:
            pieces.append(text_piece(char, allowed, everywhere, family, rng))
    pieces += distractors(family, rng.choice((0, 0, 0, 1, 2)), rng)
    rng.shuffle(pieces)
    tags = []
    name = name_piece(position["name"], template(position), names, rng)
    if rng.random() < NAME_FIRST or not pieces:
        pieces.insert(0, name)
    else:
        pieces.insert(rng.randrange(1, len(pieces) + 1), name)
        tags.append("название не первым")
    if rng.random() < 0.6:
        pieces[0] = capitalized(pieces[0])

    text = ""
    fragments: list[Fragment] = []
    chars: list[ExpectedChar] = []
    for before, piece in zip([None, *pieces], pieces, strict=False):
        if before:
            text += separator(before, piece, rng)
        for item in piece["fragments"]:
            item = item.copy()
            item["start"] += len(text)
            item["end"] += len(text)
            fragments.append(item)
        text += piece["text"]
        chars += piece["chars"]
        tags += [tag for tag in piece["tags"] if tag not in tags]
    return {
        "text": text,
        "code": position["code"],
        "fragments": fragments,
        "chars": chars,
        "noisy": any(tag in tags for tag in NAME_SPOILS) or any("kind" in c for c in chars),
        "tags": tags,
    }


def separator(before: Piece, piece: Piece, rng: Random) -> str:
    """Без знака явная пара сольётся с соседом, поэтому рядом с ней только знак."""
    if "pair" in (before["fragments"][0]["form"], piece["fragments"][0]["form"]):
        return rng.choice((", ", ", ", "; "))
    return rng.choice((", ", ", ", ", ", "; ", " "))


def allowed_values(family: list[Position]) -> dict[str, list[Value]]:
    """Значения характеристики по всем кодам шаблона: так их видит привязка."""
    allowed: dict[str, list[Value]] = {}
    for position in family:
        for char in position["characteristics"]:
            allowed.setdefault(char["name"], []).extend(char["values"])
    return allowed


def renderable(char: Characteristic) -> bool:
    if char["type"] == "number":
        unit = char.get("unit")
        return (unit is None or unit["code"] in UNITS) and bool(numbers(char))
    return any(isinstance(v, str) for v in char["values"])


def numbers(char: Characteristic) -> list[float]:
    found = []
    for value in char["values"]:
        number = inside(value)
        if number is not None:
            found.append(number)
    return found


def inside(value: Value) -> float | None:
    """Число, которое точно попадает в значение КТРУ."""
    if isinstance(value, int | float):
        return value
    if isinstance(value, str):
        return None
    low, high = value.get("min"), value.get("max")
    if low is not None and high is not None:
        return (low + high) / 2
    if low is not None:
        return low + 1
    if high is not None and high > 1:
        return high - 1
    return None


def name_piece(name: str, key: str, names: dict[str, str], rng: Random) -> Piece:
    """Люди пишут «письменный стол» и «куртка зимняя», а не название позиции целиком.
    Обрезаем, только пока название остаётся единственным среди шаблонов: иначе ответа нет."""
    tags = []
    words = name.lower().split()
    if rng.random() >= FULL_NAME:
        cuts = [size for size in range(1, head(words) + 1) if size < len(words)]
        cuts = [size for size in cuts if unique(words[:size], key, names)]
        if cuts:
            words = words[: rng.choice(cuts)]
            words[-1] = words[-1].rstrip(",")
            tags.append("название короткое")
        if len(words) >= 2 and ADJECTIVE.search(words[1]) and not ADJECTIVE.search(words[0]):
            words[0], words[1] = words[1], words[0]
            tags.append("название переставлено")
    if not tags:
        tags.append("название полное")
    text = " ".join(words)
    if rng.random() < NOISE:
        spoiled, how = spoil(text, rng)
        if spoiled != text:
            text = spoiled
            tags.append(NAME_SPOILS[0] if how == "typo" else NAME_SPOILS[1])
    if rng.random() < 0.3:
        text += " " + brand(rng)
        tags.append("бренд и модель")
    return {"text": text, "fragments": [words_fragment(text)], "chars": [], "tags": tags}


def head(words: list[str]) -> int:
    """Слова до первого предлога, скобки или запятой: «шкаф для учебных» не пишут."""
    for size, word in enumerate(words):
        if word in LINKING or word.startswith("("):
            return size
        if word.endswith(","):
            return size + 1
    return len(words)


def unique(words: list[str], key: str, names: dict[str, str]) -> bool:
    wanted = stems(" ".join(words))
    return all(not wanted <= stems(name) for other, name in names.items() if other != key)


def brand(rng: Random) -> str:
    word = "".join(rng.choice(SYLLABLES) for _ in range(rng.randint(2, 3)))
    if rng.random() < 0.3:
        return f"«{word.translate(TO_RUSSIAN).capitalize()}»"
    model = f"{word[:2].upper()}-{rng.randint(10, 9999)}"
    return f"{word.upper() if rng.random() < 0.5 else word.capitalize()} {model}"


def distractors(family: list[Position], count: int, rng: Random) -> list[Piece]:
    """Свойства вне КТРУ: из них не должна появиться характеристика КТРУ."""
    known = " ".join(c["name"].lower() for p in family for c in p["characteristics"])
    options = []
    if "цвет" not in known:
        options.append(f"цвет {rng.choice(COLORS)}")
    if "стран" not in known:
        options.append(f"производство {rng.choice(COUNTRIES)}")
    years = rng.randint(1, 5)
    options.append(f"гарантия {years} {'год' if years == 1 else 'года' if years < 5 else 'лет'}")
    pieces: list[Piece] = []
    for text in rng.sample(options, min(count, len(options))):
        fragments = [words_fragment(text)]
        digits = re.search(r"\d+", text)
        if digits:
            name = text[: digits.start()].strip()
            item = fragment("number", name, text, digits.start(), digits.end())
            item["number"] = float(digits[0])
            fragments.append(item)
        pieces.append(
            {"text": text, "fragments": fragments, "chars": [], "tags": ["свойства вне КТРУ"]}
        )
    return pieces


def text_piece(
    char: Characteristic,
    allowed: dict[str, list[Value]],
    everywhere: dict[str, list[Value]],
    family: list[Position],
    rng: Random,
) -> Piece:
    options = [v for v in char["values"] if isinstance(v, str)]
    if set(options) <= {"Да", "Нет"}:
        no = "Нет" in options and ("Да" not in options or rng.random() < 0.25)
        return flag_piece(char, "Нет" if no else "Да", rng)
    options = [v for v in options if v not in ("Да", "Нет")] or options
    values = [rng.choice(options)]
    if char["selection"] == "multiple" and len(options) >= 2 and rng.random() < 0.3:
        values = rng.sample(options, 2)
    expected: list[ExpectedChar] = [{"name": char["name"], "value": v} for v in values]
    tags = ["несколько значений"] if len(values) > 1 else []
    shown = " и ".join(written(v) for v in values)

    form = rng.choice(("pair", "named", "named", "bare", "bare", "bare"))
    if form == "bare" and not (len(values) == 1 and answerable(values[0], char, family)):
        form = "named"
    if form != "pair" and len(values) == 1 and rng.random() < 0.4:
        material = form == "bare" and "материал" in char["name"].lower()
        changed = inflected(shown, material, rng)
        if changed != shown:
            shown = changed
            tags.append("другая форма слова")
    if rng.random() < NOISE and len(values) == 1:
        shown, kind = spoil_value(shown, allowed[char["name"]], everywhere[char["name"]], form, rng)
        if kind == "not_in_list":
            expected[0]["value"] = shown
        if kind:
            expected[0]["kind"] = kind
            tags.append(KIND_TAGS[kind])
    if form == "pair":
        return pair_piece(char["name"], shown, expected, tags)
    if form == "bare":
        text = shown
        tags.append("значение без названия")
    else:
        text = f"{spoken_name(char, family, tags, rng)} {shown}"
        tags.append("название и значение")
    return {"text": text, "fragments": [words_fragment(text)], "chars": expected, "tags": tags}


def inflected(value: str, material: bool, rng: Random) -> str:
    """Значение в форме, согласованной с товаром: «Деревянный» → «деревянная»,
    материал после «из»: «Чугун» → «из чугуна». Ответ остаётся словом КТРУ.
    После названия характеристики «из» не ставят: «материал из чугуна» не пишут."""
    if " " in value:
        return value
    if ADJECTIVE.search(value):
        stem = value[:-2]
        if value.endswith("ий") and stem[-1:] not in "кгхжшчщ":
            return stem + rng.choice(("яя", "ее", "ие"))
        return stem + rng.choice(("ая", "ое", "ые"))
    if not material:
        return value
    if value.isupper():
        return f"из {value}"
    if value[-1] in "аь":
        ending = "и" if value[-1] == "ь" or value[-2:-1] in "кгхжшчщ" else "ы"
        return f"из {value[:-1]}{ending}"
    if value[-1] not in "еиоуыэюя":
        return f"из {value}а"
    return value


def spoken_name(char: Characteristic, family: list[Position], tags: list[str], rng: Random) -> str:
    """Название характеристики так, как его говорят: без уточнений в скобках и после
    запятой, без первого прилагательного («Российский размер» → «размер»), пока оно не
    совпадает с другой характеристикой шаблона."""
    name = char["name"].lower()
    short = re.sub(r"\s*\(.*?\)|,.*$", "", name).strip()
    words = short.split()
    if len(words) >= 2 and ADJECTIVE.search(words[0]):
        short = " ".join(words[1:])
    if short == name or rng.random() < 0.5:
        return name
    wanted = stems(short)
    for position in family:
        for other in position["characteristics"]:
            if other["name"] != char["name"] and wanted <= stems(other["name"]):
                return name
    tags.append("название характеристики короче")
    return short


def pair_piece(name: str, value: str, expected: list[ExpectedChar], tags: list[str]) -> Piece:
    text = f"{name}: {value}"
    item = fragment("pair", name, text, 0, len(text))
    item["value"] = value
    return {"text": text, "fragments": [item], "chars": expected, "tags": tags + ["пара"]}


def flag_piece(char: Characteristic, value: str, rng: Random) -> Piece:
    """«Наличие подсветки: Да» пишут «подсветка» или «с подсветкой», «Нет» пишут «без
    подсветки». Слово «наличие» люди почти не пишут."""
    expected: list[ExpectedChar] = [{"name": char["name"], "value": value}]
    tags = ["признак нет"] if value == "Нет" else []
    name = char["name"].lower()
    words = name.split()
    rest = " ".join(words[1:]) if len(words) >= 2 and words[0] in FLAG_WORDS else ""
    roll = rng.random()
    if rest and roll < 0.6:
        text = f"без {rest}" if value == "Нет" else rest
        tags.append("признак без «наличие»")
    elif value == "Да" and roll < 0.85:
        text = name
    else:
        return pair_piece(char["name"], value.lower(), expected, tags)
    return {"text": text, "fragments": [words_fragment(text)], "chars": expected, "tags": tags}


def written(value: str) -> str:
    """Аббревиатуры пишут как есть («ЛДСП»), остальное строчными."""
    return value if value[1:] != value[1:].lower() else value.lower()


def answerable(value: str, char: Characteristic, family: list[Position]) -> bool:
    """Без названия значение понятно, только если оно словесное и бывает у одной
    характеристики шаблона: «70» или «несъемный» без названия угадать нельзя."""
    if len(re.findall(r"[а-яёa-z]", value, re.IGNORECASE)) < 3:
        return False
    owners = set()
    for position in family:
        for other in position["characteristics"]:
            if any(
                isinstance(v, str) and v.casefold() == value.casefold() for v in other["values"]
            ):
                owners.add(other["name"])
    return owners == {char["name"]}


def spoil_value(
    shown: str, allowed: list[Value], everywhere: list[Value], form: str, rng: Random
) -> tuple[str, FixKind | None]:
    """Значение не из списка только в явной паре: без названия его не к чему привязать."""
    known = {v.casefold() for v in allowed if isinstance(v, str)}
    if form == "pair" and rng.random() < 0.3:
        other = foreign(known, everywhere, rng)
        if other:
            return other, "not_in_list"
    spoiled, how = spoil(shown, rng)
    if spoiled == shown or spoiled.casefold() in known:
        return shown, None
    return spoiled, how


def foreign(known: set[str], everywhere: list[Value], rng: Random) -> str | None:
    """Значение той же характеристики у другой позиции, не похожее ни на одно из списка:
    похожее было бы опечаткой."""
    pool = sorted({v.lower() for v in everywhere if isinstance(v, str)} - known - {"да", "нет"})
    rng.shuffle(pool)
    for value in pool:
        if all(SequenceMatcher(None, value, k).ratio() < 0.75 for k in known):
            return value
    return None


def spoil(text: str, rng: Random) -> tuple[str, FixKind]:
    roll = rng.random()
    if roll < 0.7:
        return typo(text, rng), "typo"
    words = text.split()
    long = [i for i, word in enumerate(words) if len(word) >= 3 and word.isalpha()]
    if not long:
        return text, "typo"
    index = rng.choice(long)
    table = TO_LATIN if roll < 0.85 else TO_TWINS
    words[index] = words[index].lower().translate(table)
    return " ".join(words), "layout"


def typo(text: str, rng: Random) -> str:
    """Одна живая опечатка в слове от четырёх букв, первая буква остаётся."""
    words = text.split()
    long = [i for i, word in enumerate(words) if len(word) >= 4 and word.isalpha()]
    if not long:
        return text
    index = rng.choice(long)
    words[index] = typo_word(words[index], rng.choice(TYPOS), rng)
    return " ".join(words)


def typo_word(word: str, how: str, rng: Random) -> str:
    places = range(1, len(word) - 1)
    if how == "vowel":
        vowels = [i for i in places if word[i] in VOWELS]
        if vowels:
            i = rng.choice(vowels)
            return word[:i] + VOWELS[word[i]] + word[i + 1 :]
    if how == "double":
        doubled = [i for i in places if word[i] == word[i + 1]]
        if doubled:
            i = rng.choice(doubled)
            return word[:i] + word[i + 1 :]
        i = rng.choice(places)
        return word[: i + 1] + word[i:]
    if how == "swap" and word[1] != word[2]:
        return word[0] + word[2] + word[1] + word[3:]
    if how == "key":
        i = rng.choice(places)
        for row in KEY_ROWS:
            at = row.find(word[i])
            if at >= 0:
                near = row[max(at - 1, 0) : at] + row[at + 1 : at + 2]
                return word[:i] + rng.choice(near) + word[i + 1 :]
    i = rng.choice(places)
    return word[:i] + word[i + 1 :]


def counted_piece(char: Characteristic, count: int, expected: ExpectedChar) -> Piece:
    """«Количество полок: 5» пишут «5 полок»: родительный падеж уже в названии КТРУ."""
    text = f"{count} {char['name'][len('Количество ') :].lower()}"
    item = fragment("number", None, text, 0, len(str(count)))
    item["number"] = count
    fragments = [words_fragment(text), item]
    return {
        "text": text,
        "fragments": fragments,
        "chars": [expected],
        "tags": ["счёт перед словом"],
    }


def size_pair(chosen: list[Characteristic]) -> tuple[Characteristic, Characteristic] | None:
    """Длина и ширина одного и того же: «Длина столешницы» и «Ширина столешницы».
    Другие сочетания без названий не понять: где высота, а где глубина."""
    for length in chosen:
        if length["type"] != "number" or not length["name"].startswith("Длина"):
            continue
        rest = length["name"][len("Длина") :]
        for width in chosen:
            if width["name"] == "Ширина" + rest and width.get("unit") == length.get("unit"):
                return length, width
    return None


def sizes_piece(first: Characteristic, second: Characteristic, rng: Random) -> Piece:
    """Габариты одной записью: «1200х800 мм», «90 на 200»."""
    unit = first["unit"]["code"] if "unit" in first else None
    numbers_shown = [rng.choice(numbers(first)), rng.choice(numbers(second))]
    texts = [show(tidy(n)) for n in numbers_shown]
    joiner = rng.choice(("х", "x", " на ", "*"))
    text = joiner.join(texts) + (" " + UNITS[unit] if unit and rng.random() < 0.7 else "")
    fragments = [words_fragment(text)]
    expected: list[ExpectedChar] = []
    at = 0
    for char, number, number_text in zip((first, second), numbers_shown, texts, strict=True):
        start = text.index(number_text, at)
        at = start + len(number_text)
        end = len(text) if char is second else at
        item = fragment("number", text[:start].strip() or None, text, start, end)
        item["number"], item["unit"] = tidy(number), unit
        fragments.append(item)
        expected.append({"name": char["name"], "value": tidy(number)})
    return {"text": text, "fragments": fragments, "chars": expected, "tags": ["размеры через х"]}


def number_piece(
    char: Characteristic, allowed: dict[str, list[Value]], family: list[Position], rng: Random
) -> Piece:
    """Число в живой записи: «450мм», «0,45 м», «до 13 мм», «от 170 до 176», «два».
    Порча единицы и значения только там, где ответ однозначен."""
    unit_code = char["unit"]["code"] if "unit" in char else None
    picked = rng.choice([v for v in char["values"] if inside(v) is not None])
    number = inside(picked)
    assert number is not None
    expected: ExpectedChar = {"name": char["name"], "value": tidy(number)}
    tags: list[str] = []
    counted = char["name"].startswith("Количество ") and unit_code in (None, "796")
    if counted and float(number).is_integer() and rng.random() < 0.5:
        return counted_piece(char, int(number), expected)
    name = spoken_name(char, family, tags, rng)
    shown, unit = [number], unit_code
    if unit_code and rng.random() < NOISE:
        spoiled, unit, kind = spoil_number(number, unit_code, bounds(allowed[char["name"]]), rng)
        shown = [spoiled]
        if kind:
            expected["kind"] = kind
            tags.append(KIND_TAGS[kind])
        if kind == "out_of_range":
            expected["value"] = tidy(spoiled)
    if rng.random() < 0.1:
        name = typo(name, rng)
        tags.append("опечатка в названии характеристики")

    clean = "kind" not in expected
    texts = [written_number(shown[0], rng)]
    lead = ""
    if clean and isinstance(picked, dict) and "min" in picked and "max" in picked:
        if rng.random() < 0.2:
            expected["value"] = show(picked)
            shown = [picked["min"], picked["max"]]
            texts = [written_number(value, rng) for value in shown]
            tags.append("диапазон")
    elif clean and upper_only(picked, char["name"]) and rng.random() < 0.3:
        lead = "до "
        tags.append("до числа")
    whole = float(shown[0]).is_integer() and 0 < shown[0] < len(NUMBER_WORDS)
    if clean and len(shown) == 1 and whole and rng.random() < 0.15:
        texts = [NUMBER_WORDS[int(shown[0])]]
        tags.append("число словами")

    unit_text = ""
    if unit:
        roll = rng.random()
        if roll < 0.2 and unit == unit_code:
            unit = None
            tags.append("число без единицы")
        elif roll < 0.4 and texts[-1][0].isdigit():
            unit_text = UNITS[unit]
            tags.append("единица слитно")
        else:
            unit_text = " " + UNITS[unit]
    if len(texts) == 2:
        value_text = rng.choice(("от {} до {}", "{}-{}", "{} - {}")).format(*texts)
    else:
        value_text = lead + texts[0]
    value_text += unit_text

    if len(texts) == 1 and rng.random() < 0.1:
        piece = pair_piece(char["name"], value_text, [expected], tags)
        piece["fragments"][0]["number"], piece["fragments"][0]["unit"] = tidy(shown[0]), unit
        return piece
    text = f"{name} {value_text}"
    fragments = [words_fragment(text)]
    at = len(name) + 1
    for index, (value, number_text) in enumerate(zip(shown, texts, strict=True)):
        start = text.index(number_text, at)
        at = start + len(number_text)
        end = len(text) if index == len(texts) - 1 else at
        item = fragment("number", text[:start].strip(), text, start, end)
        item["number"], item["unit"] = tidy(value), unit
        fragments.append(item)
    return {"text": text, "fragments": fragments, "chars": [expected], "tags": tags}


def upper_only(picked: Value, name: str) -> bool:
    """«До 13 мм» пишут о наибольшем значении, у остальных «до» меняет смысл."""
    return name.startswith("Максимальн") or (isinstance(picked, dict) and "min" not in picked)


def written_number(number: float, rng: Random) -> str:
    shown = show(tidy(number))
    return shown.replace(".", ",") if rng.random() < 0.7 else shown


def spoil_number(
    number: float, unit_code: str, corridor: tuple[float, float] | None, rng: Random
) -> tuple[float, str, FixKind | None]:
    """Другая единица той же величины, сдвиг на порядок при той же единице или число,
    которого быть не может («стол 5 м»). Сдвиг и выход за коридор берём, только если
    ответ восстанавливается однозначно."""
    choices: list[tuple[float, str, FixKind]] = []
    other = other_unit(unit_code)
    if other:
        choices.append((number * SCALE[unit_code][1] / SCALE[other][1], other, "unit"))
    if corridor and all(abs(edge) != float("inf") for edge in corridor):
        for shift in SHIFTS:
            spoiled = tidy(number / shift)
            fits = [s for s in SHIFTS if within(spoiled * s, corridor)]
            if not within(spoiled, corridor) and fits == [shift]:
                choices.append((spoiled, unit_code, "wrong_unit"))
                break
        far = tidy(corridor[1] * rng.choice((3, 5, 7)))
        if far > 0 and not any(within(far * s, corridor) for s in SHIFTS):
            choices.append((far, unit_code, "out_of_range"))
    if not choices:
        return number, unit_code, None
    return rng.choice(choices)


def within(value: float, corridor: tuple[float, float]) -> bool:
    return corridor[0] <= value <= corridor[1]


def other_unit(unit_code: str) -> str | None:
    """Соседняя единица той же величины: так пишут чаще всего, мм вместо см и наоборот."""
    if unit_code not in SCALE:
        return None
    dimension = SCALE[unit_code][0]
    same = [code for code in SCALE if SCALE[code][0] == dimension and code != unit_code]
    for code in same:
        if code in UNITS and 0.001 <= SCALE[code][1] / SCALE[unit_code][1] <= 1000:
            return code
    return None


def capitalized(piece: Piece) -> Piece:
    """Первая буква текста заглавная, куски с начала текста меняются вместе с ним."""
    text = piece["text"][0].upper() + piece["text"][1:]
    fragments = []
    for item in piece["fragments"]:
        item = item.copy()
        item["text"] = text[item["start"] : item["end"]]
        if item["form"] == "words":
            item["value"] = item["text"]
        if item["name"]:
            item["name"] = text[: len(item["name"])]
        fragments.append(item)
    return {"text": text, "fragments": fragments, "chars": piece["chars"], "tags": piece["tags"]}


def fragment(
    form: Literal["pair", "number", "words"],
    name: str | None,
    text: str,
    start: int,
    end: int,
) -> Fragment:
    return {
        "form": form,
        "name": name,
        "value": text[start:end],
        "text": text[start:end],
        "number": None,
        "unit": None,
        "start": start,
        "end": end,
    }


def words_fragment(text: str) -> Fragment:
    return fragment("words", None, text, 0, len(text))


def siblings(position: Position, by_code: dict[str, Position]) -> list[Candidate]:
    """Все конечные коды шаблона: правильный вход сужению, ему остаётся различить соседей."""
    parent = position["parent_code"]
    if parent is None:
        family = [position]
    else:
        family = []
        for p in by_code.values():
            if p["parent_code"] == parent and p["position_type"] == "final":
                family.append(p)
    score = 1 / len(family)
    return [{"code": p["code"], "name": p["name"], "score": score} for p in family]


def gold_char(char: ExpectedChar) -> FoundChar:
    return {
        "name": char["name"],
        "value": char["value"],
        "unit": None,
        "source": "ktru",
        "fragment": "",
        "start": 0,
        "end": 0,
        "required": False,
        "fix": None,
        "kind": None,
        "rule": "gold",
    }


def section_texts(
    portal: Portal, count: int, seed: int = 0, test: bool = False
) -> list[tuple[str, str]]:
    """Название раздела в той же живой форме, что и название позиции. Раздел во
    множественном числе, а люди пишут в единственном, поэтому цифра этапа 2 всё равно
    выше, чем на живых текстах."""
    rng = Random(seed)
    names = sorted(name for name in portal if held_out(name) == test)
    every = {name: name for name in portal}
    found = []
    for name in rng.sample(names, min(count, len(names))):
        text = name_piece(name, name, every, rng)["text"]
        if rng.random() < 0.3:
            text += ", " + distractors([], 1, rng)[0]["text"]
        found.append((text, name))
    return found


if __name__ == "__main__":
    from scripts.dataloader import load_data

    count = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    print(json.dumps(generate(load_data(), count), ensure_ascii=False, indent=2))
