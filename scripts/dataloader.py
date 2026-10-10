import argparse
import json
from datetime import date
from pathlib import Path
from typing import cast

from contracts.ref import Portal, Position

DEFAULT_DATA_PATH = Path("data/processed/ktru.json")
DEFAULT_PORTAL_PATH = Path("data/processed/portal.json")
CATEGORIES = ("clothing", "furniture", "tools")


def load_data(
    path: Path = DEFAULT_DATA_PATH,
    *,
    expected_date: date | None = None,
    category: str | None = None,
    final_only: bool = False,
) -> list[Position]:
    with path.open(encoding="utf-8") as source:
        payload = json.load(source)

    metadata = payload.get("metadata", {})
    source_date = metadata.get("source_date")
    if expected_date is not None and source_date != expected_date.isoformat():
        raise ValueError(
            f"Ожидалась выгрузка за {expected_date.isoformat()}, найдена за {source_date}"
        )

    positions = payload.get("positions")
    if not isinstance(positions, list):
        raise ValueError("В файле нет массива positions")
    return [
        position
        for position in positions
        if (category is None or position["category"] == category)
        and (not final_only or position["position_type"] == "final")
    ]


def load_portal(path: Path = DEFAULT_PORTAL_PATH) -> Portal:
    with path.open(encoding="utf-8") as source:
        payload = json.load(source)

    if not isinstance(payload, dict) or not payload:
        raise ValueError("Файл портала должен содержать категории")
    for category, characteristics in payload.items():
        if not isinstance(category, str) or not category:
            raise ValueError("Название категории портала должно быть непустой строкой")
        if not isinstance(characteristics, list):
            raise ValueError(f"Характеристики {category} должны быть массивом")
        for characteristic in characteristics:
            if not isinstance(characteristic, dict) or set(characteristic) != {
                "name",
                "type",
                "unit",
                "values",
            }:
                raise ValueError(f"Некорректная характеристика портала в {category}")
            if not isinstance(characteristic["name"], str) or not characteristic["name"]:
                raise ValueError(f"У характеристики {category} нет названия")
            if not isinstance(characteristic["type"], str) or not characteristic["type"]:
                raise ValueError(f"У характеристики {characteristic['name']} нет типа")
            if characteristic["unit"] is not None and not isinstance(characteristic["unit"], str):
                raise ValueError(f"Некорректная единица у {characteristic['name']}")
            if not isinstance(characteristic["values"], list) or not all(
                isinstance(value, str) and value for value in characteristic["values"]
            ):
                raise ValueError(f"Некорректные значения у {characteristic['name']}")
    return cast(Portal, payload)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Читает рабочий справочник КТРУ.")
    parser.add_argument("--path", type=Path, default=DEFAULT_DATA_PATH)
    parser.add_argument("--expected-date", type=date.fromisoformat)
    parser.add_argument("--category", choices=CATEGORIES)
    parser.add_argument("--final-only", action="store_true")
    parser.add_argument("--limit", type=int, default=1)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.limit < 1:
        raise SystemExit("--limit должен быть больше нуля")
    try:
        data = load_data(
            args.path,
            expected_date=args.expected_date,
            category=args.category,
            final_only=args.final_only,
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f"Ошибка: {error}") from error
    print(json.dumps(data[: args.limit], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
