import re
from collections.abc import Iterable
from difflib import get_close_matches
from typing import Literal, TypedDict

MIN_SHARE = 0.5
STOP = {"для", "или", "без", "под", "над", "при", "что"}
LATIN_KEYS = "qwertyuiop[]asdfghjkl;'zxcvbnm,."
RUSSIAN_KEYS = "йцукенгшщзхъфывапролджэячсмитьбю"
LAYOUT = str.maketrans(LATIN_KEYS + LATIN_KEYS.upper(), RUSSIAN_KEYS + RUSSIAN_KEYS.upper())
TWINS = str.maketrans("aceopxyABCEHKMOPTX", "асеорхуАВСЕНКМОРТХ")
LATIN = re.compile(r"[a-z]", re.IGNORECASE)
CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)
# Фраза кончается на запятой, точке с запятой, переносе или точке с пробелом: «2.5» не конец.
PAIR = re.compile(
    r"(?:^|(?<=[,;\n])|(?<=\.\s))\s*"
    r"(?P<name>(?:[^,;\n:.]|\.(?!\s))+?)\s*:\s*(?P<value>(?:[^,;\n:.]|\.(?!\s))+)"
)


class Word(TypedDict):
    start: int
    end: int
    text: str
    stem: str
    fix: Literal["typo", "layout"] | None


def stem(word: str) -> str:
    """Грубая основа вместо морфологии: «стола» и «стол» дают «сто»."""
    lowered = word.lower().replace("ё", "е")
    return lowered[: min(6, max(3, len(lowered) - 2))]


def russian(word: str) -> tuple[str, bool]:
    """Латиница в русском слове или целиком не та раскладка: «cnjk» → «стол». Длина та же."""
    if not LATIN.search(word):
        return word, False
    if CYRILLIC.search(word):
        return word.translate(TWINS), True
    return word.translate(LAYOUT), True


def words(text: str) -> list[Word]:
    found: list[Word] = []
    for match in re.finditer(r"\w+", text):
        word, changed = russian(match[0])
        if len(word) >= 3 and not word.isdigit() and word.lower() not in STOP:
            found.append(
                {
                    "start": match.start(),
                    "end": match.end(),
                    "text": word.lower(),
                    "stem": stem(word),
                    "fix": "layout" if changed else None,
                }
            )
    return found


def stems(text: str) -> set[str]:
    return {word["stem"] for word in words(text)}


def vocabulary(texts: Iterable[str]) -> dict[str, str]:
    vocab = {}
    for text in texts:
        for word in words(text):
            vocab[word["text"]] = word["stem"]
    return vocab


def correct(found: list[Word], vocab: dict[str, str]) -> list[Word]:
    """Слово не из словаря меняем на ближайшее по написанию: «деревяный» → «деревянный»."""
    known = set(vocab.values())
    fixed: list[Word] = []
    for word in found:
        close = []
        if word["text"] not in vocab and len(word["text"]) >= 5:
            # Своя основа совпала, значит это скорее другая форма слова, а не опечатка.
            cutoff = 0.85 if word["stem"] in known else 0.8
            close = get_close_matches(word["text"], vocab, n=1, cutoff=cutoff)
        if close:
            word = word.copy()
            word["stem"] = vocab[close[0]]
            word["fix"] = word["fix"] or "typo"
        fixed.append(word)
    return fixed


def unknown(text: str, covered: list[tuple[int, int]], known: set[str]) -> list[str]:
    """Слова, которые не стали ни товаром, ни характеристикой: их видно, а не выброшено."""
    left = []
    for word in words(text):
        inside = any(start <= word["start"] < end for start, end in covered)
        if not inside and word["stem"] not in known:
            left.append(text[word["start"] : word["end"]])
    return left


def without_pairs(text: str) -> str:
    """Дописанные «Название: значение» уточняют характеристики, а не вид товара."""
    kept = text
    for match in PAIR.finditer(text):
        if text[: match.start()].strip():
            kept = kept[: match.start()] + " " * (match.end() - match.start()) + kept[match.end() :]
    return kept


def best_named[T](text: str, items: list[T], names: list[str]) -> list[T]:
    """Все элементы с лучшим совпадением названия, пусто, если совпало меньше половины слов."""
    vocab = vocabulary(names)
    text_stems = {word["stem"] for word in correct(words(without_pairs(text)), vocab)}
    if not text_stems:
        return []
    ranked = []
    for item, name in zip(items, names, strict=True):
        name_stems = stems(name)
        if name_stems:
            ranked.append((rank(name_stems, text_stems), item))
    if not ranked:
        return []
    best = max(item_rank for item_rank, _ in ranked)
    if best[0] < MIN_SHARE:
        return []
    return [item for item_rank, item in ranked if item_rank == best]


def rank(name_stems: set[str], text_stems: set[str]) -> tuple[float, int, int]:
    """Больше лучше: доля совпавших слов, затем их число, затем меньше лишних в названии."""
    hits = len(name_stems & text_stems)
    # Доля от меньшего из двух, чтобы короткий запрос «Перчатки» находил длинные названия.
    share = hits / min(len(name_stems), len(text_stems))
    extra = len(name_stems) - hits
    return share, hits, -extra
