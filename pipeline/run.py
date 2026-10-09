import json
import sys

from contracts.pipeline import ExtractChars, FindCandidates, Narrow, Result
from pipeline import candidates, chars, narrow

find_candidates: FindCandidates = candidates.find
extract_chars: ExtractChars = chars.extract
narrow_codes: Narrow = narrow.narrow

# Заглушка для веба и замера, пока шаги не подключены.
STUB: Result = {
    "category": "furniture",
    "steps": [
        {"what": "Название похоже на «Стол письменный»", "left": 2},
        {"what": "Тип каркаса: Деревянный", "left": 1},
    ],
    "chars": [
        {
            "name": "Тип каркаса",
            "value": "Деревянный",
            "unit": None,
            "source": "ktru",
            "fragment": "деревянный",
        },
        {
            "name": "Вид материала столешницы",
            "value": "ЛДСП",
            "unit": None,
            "source": "ktru",
            "fragment": "столешница ЛДСП",
        },
        {
            "name": "Толщина материала столешницы",
            "value": 22,
            "unit": {"code": "003", "name": "Миллиметр"},
            "source": "ktru",
            "fragment": "22 мм",
        },
        {
            "name": "Цвет",
            "value": "Венге",
            "unit": None,
            "source": "portal",
            "fragment": "цвет венге",
        },
    ],
    "code": "31.01.10.000-00000004",
    "question": None,
    "missing_required": ["Конфигурация стола", "Назначение стола письменного"],
    "remarks": [{"was": "22 мм", "now": "22 Миллиметр", "why": "единица из КТРУ"}],
}


def run(text: str) -> Result:
    return STUB


if __name__ == "__main__":
    print(json.dumps(run(" ".join(sys.argv[1:])), ensure_ascii=False, indent=2))
