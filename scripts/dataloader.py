import argparse
import gzip
import json
import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path

DEFAULT_DATA_PATH = Path("data/processed/ktru_actual.jsonl.gz")
ARCHIVE_DATE_PATTERN = re.compile(r"_(\d{4}-\d{2}-\d{2})\.zip$")


def open_data(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open(encoding="utf-8")


def source_date(record: dict) -> date:
    archive = record.get("_source", {}).get("archive", "")
    match = ARCHIVE_DATE_PATTERN.search(archive)
    if match is None:
        raise ValueError(f"Не удалось определить дату выгрузки из {archive!r}")
    return date.fromisoformat(match.group(1))


def iso_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value[:10])


def is_applicable(data: dict, on_date: date) -> bool:
    if data.get("status") != "ACTIVE":
        return False
    start = iso_date(data.get("applicationDateStart"))
    end = iso_date(data.get("applicationDateEnd"))
    return (start is None or start <= on_date) and (end is None or on_date <= end)


def iter_data(
    path: Path = DEFAULT_DATA_PATH,
    *,
    on_date: date | None = None,
    expected_date: date | None = None,
) -> Iterator[dict]:
    with open_data(path) as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                data = record["data"]
            except (json.JSONDecodeError, KeyError, TypeError) as error:
                raise ValueError(f"Некорректная запись JSONL в строке {line_number}") from error
            if expected_date is not None and source_date(record) != expected_date:
                raise ValueError(
                    f"Строка {line_number}: ожидалась выгрузка за {expected_date.isoformat()}"
                )
            if on_date is None or is_applicable(data, on_date):
                yield data


def load_data(
    path: Path = DEFAULT_DATA_PATH,
    *,
    on_date: date | None = None,
    expected_date: date | None = None,
) -> list[dict]:
    return list(iter_data(path, on_date=on_date, expected_date=expected_date))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Читает data из объединённого КТРУ.")
    parser.add_argument("--path", type=Path, default=DEFAULT_DATA_PATH)
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        help="Оставить позиции, действующие на дату YYYY-MM-DD.",
    )
    parser.add_argument(
        "--expected-date",
        type=date.fromisoformat,
        help="Проверить дату исходной выгрузки (YYYY-MM-DD).",
    )
    parser.add_argument("--limit", type=int, default=1)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.limit < 1:
        raise SystemExit("--limit должен быть больше нуля")

    result = []
    for data in iter_data(args.path, on_date=args.date, expected_date=args.expected_date):
        result.append(data)
        if len(result) == args.limit:
            break
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
