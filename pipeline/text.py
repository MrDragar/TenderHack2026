import re
from typing import TypedDict

STOP = {"для", "или", "без", "под", "над", "при", "что"}


class Word(TypedDict):
    start: int
    end: int
    stem: str


def stem(word: str) -> str:
    """Грубая основа вместо морфологии: «стола» и «стол» дают «сто»."""
    lowered = word.lower().replace("ё", "е")
    return lowered[: min(6, max(3, len(lowered) - 2))]


def words(text: str) -> list[Word]:
    found: list[Word] = []
    for match in re.finditer(r"\w+", text):
        word = match[0]
        if len(word) >= 3 and not word.isdigit() and word.lower() not in STOP:
            found.append({"start": match.start(), "end": match.end(), "stem": stem(word)})
    return found


def stems(text: str) -> set[str]:
    return {word["stem"] for word in words(text)}
