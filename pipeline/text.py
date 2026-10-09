import re

STOP = {"для", "или", "без", "под", "над", "при", "что"}


def stem(word: str) -> str:
    # Грубая замена морфологии: «стола» и «стол», «письменного» и «письменный» дают одну основу.
    lowered = word.lower().replace("ё", "е")
    return lowered[: min(6, max(3, len(lowered) - 2))]


def words(text: str) -> list[tuple[int, int, str]]:
    found = []
    for match in re.finditer(r"\w+", text):
        word = match[0]
        if len(word) >= 3 and not word.isdigit() and word.lower() not in STOP:
            found.append((match.start(), match.end(), stem(word)))
    return found


def stems(text: str) -> set[str]:
    return {s for _, _, s in words(text)}
