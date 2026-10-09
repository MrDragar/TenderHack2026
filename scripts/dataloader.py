import argparse
import json
from datetime import date
from pathlib import Path

DEFAULT_DATA_PATH = Path("data/processed/ktru.json")
CATEGORIES = ("clothing", "furniture", "tools")


def load_data(
    path: Path = DEFAULT_DATA_PATH,
    *,
    expected_date: date | None = None,
    category: str | None = None,
    final_only: bool = False,
) -> list[dict]:
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
