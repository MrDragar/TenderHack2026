import json
import sys
from random import Random
from typing import Literal, TypedDict

from contracts.eval import ExpectedChar
from contracts.pipeline import Candidate, FoundChar, Fragment
from contracts.ref import Characteristic, Portal, Position, Value
from pipeline.text import LATIN_KEYS, RUSSIAN_KEYS
from pipeline.values import SCALE, UNIT_ALIASES, show, tidy

CHARS_PER_TEXT = 3
NOISE = 0.3
TO_LATIN = str.maketrans(RUSSIAN_KEYS, LATIN_KEYS)
ALIAS = {}
for alias, unit_code in UNIT_ALIASES.items():
    ALIAS.setdefault(unit_code, alias)


class Generated(TypedDict):
    text: str
    code: str
    fragments: list[Fragment]
    chars: list[ExpectedChar]
    noisy: bool


class Piece(TypedDict):
    text: str
    fragments: list[Fragment]
    char: ExpectedChar | None


def generate(ref: list[Position], count: int, seed: int = 0) -> list[Generated]:
    """Тексты с известным ответом: позиция, куски, что из них должно получиться и какая
    порча внесена. Порча из таблицы architecture.md, кроме синонимов: их списка нет."""
    rng = Random(seed)
    finals = [p for p in ref if p["position_type"] == "final"]
    found = []
    for position in rng.sample(finals, min(count, len(finals))):
        found.append(one(position, rng))
    return found


def one(position: Position, rng: Random) -> Generated:
    usable = [char for char in position["characteristics"] if renderable(char)]
    chosen = rng.sample(usable, min(CHARS_PER_TEXT, len(usable)))
    pieces = [name_piece(position["name"], rng)]
    for char in chosen:
        if char["type"] == "number":
            pieces.append(number_piece(char, rng))
        else:
            pieces.append(text_piece(char, rng))

    text = ""
    fragments: list[Fragment] = []
    chars: list[ExpectedChar] = []
    for piece in pieces:
        if text:
            text += ", "
        for item in piece["fragments"]:
            item = item.copy()
            item["start"] += len(text)
            item["end"] += len(text)
            fragments.append(item)
        text += piece["text"]
        if piece["char"]:
            chars.append(piece["char"])
    spoiled_name = pieces[0]["text"] != position["name"].lower()
    noisy = spoiled_name or any("kind" in char for char in chars)
    return {
        "text": text,
        "code": position["code"],
        "fragments": fragments,
        "chars": chars,
        "noisy": noisy,
    }


def renderable(char: Characteristic) -> bool:
    if char["type"] == "number":
        return "unit" in char and char["unit"]["code"] in ALIAS and bool(numbers(char))
    return any(isinstance(v, str) and v != "Нет" for v in char["values"])


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


def name_piece(name: str, rng: Random) -> Piece:
    text = name.lower()
    if rng.random() < NOISE:
        text = spoil_word(text, rng)
    return {"text": text, "fragments": [words_fragment(text, 0)], "char": None}


def text_piece(char: Characteristic, rng: Random) -> Piece:
    options = [v for v in char["values"] if isinstance(v, str) and v != "Нет"]
    value = rng.choice(options)
    expected: ExpectedChar = {"name": char["name"], "value": value}
    shown = char["name"].lower() if value == "Да" else value.lower()
    if rng.random() < NOISE and value != "Да":
        spoiled = typo(shown, rng)
        if spoiled != shown:
            shown = spoiled
            expected["kind"] = "typo"
    # «Наличие пюпитра: наличие пюпитра» никто не пишет, признак называют одним названием.
    if rng.random() < 0.5 and value != "Да":
        text = f"{char['name']}: {shown}"
        item = fragment("pair", char["name"], shown, text, 0, len(text))
        return {"text": text, "fragments": [item], "char": expected}
    return {"text": shown, "fragments": [words_fragment(shown, 0)], "char": expected}


def number_piece(char: Characteristic, rng: Random) -> Piece:
    unit_code = char["unit"]["code"]
    number = rng.choice(numbers(char))
    expected: ExpectedChar = {"name": char["name"], "value": tidy(number)}
    shown_number, shown_unit = number, unit_code
    other = other_unit(unit_code)
    if other and rng.random() < NOISE:
        shown_number = number * SCALE[unit_code][1] / SCALE[other][1]
        shown_unit = other
        expected["kind"] = "unit"
    value = f"{show(tidy(shown_number)).replace('.', ',')} {ALIAS[shown_unit]}"
    name = char["name"].lower()
    text = f"{name} {value}"
    number_item = fragment("number", name, value, text, len(name) + 1, len(text))
    number_item["number"], number_item["unit"] = tidy(shown_number), shown_unit
    return {"text": text, "fragments": [words_fragment(text, 0), number_item], "char": expected}


def other_unit(unit_code: str) -> str | None:
    """Соседняя единица той же величины: так пишут чаще всего, мм вместо см и наоборот."""
    if unit_code not in SCALE:
        return None
    dimension = SCALE[unit_code][0]
    same = [code for code in SCALE if SCALE[code][0] == dimension and code != unit_code]
    for code in same:
        if code in ALIAS and 0.001 <= SCALE[code][1] / SCALE[unit_code][1] <= 1000:
            return code
    return None


def spoil_word(text: str, rng: Random) -> str:
    if rng.random() < 0.5:
        return typo(text, rng)
    words = text.split()
    index = rng.randrange(len(words))
    words[index] = words[index].translate(TO_LATIN)
    return " ".join(words)


def typo(text: str, rng: Random) -> str:
    """Пропущенная буква в длинном слове: «деревяный»."""
    words = text.split()
    long = [i for i, word in enumerate(words) if len(word) >= 6]
    if not long:
        return text
    index = rng.choice(long)
    word = words[index]
    cut = rng.randrange(1, len(word) - 1)
    words[index] = word[:cut] + word[cut + 1 :]
    return " ".join(words)


def fragment(
    form: Literal["pair", "number", "words"],
    name: str | None,
    value: str,
    text: str,
    start: int,
    end: int,
) -> Fragment:
    return {
        "form": form,
        "name": name,
        "value": value,
        "text": text[start:end],
        "number": None,
        "unit": None,
        "start": start,
        "end": end,
    }


def words_fragment(text: str, start: int) -> Fragment:
    return fragment("words", None, text, text, start, start + len(text))


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
    }


def section_texts(portal: Portal, count: int, seed: int = 0) -> list[tuple[str, str]]:
    """Название раздела с порчей. Раздел во множественном числе, а люди пишут в единственном,
    поэтому цифра этапа 2 на таких текстах выше, чем на живых."""
    rng = Random(seed)
    names = sorted(portal)
    found = []
    for name in rng.sample(names, min(count, len(names))):
        text = name.lower()
        if rng.random() < NOISE:
            text = spoil_word(text, rng)
        found.append((text, name))
    return found


if __name__ == "__main__":
    from scripts.dataloader import load_data

    count = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    print(json.dumps(generate(load_data(), count), ensure_ascii=False, indent=2))
