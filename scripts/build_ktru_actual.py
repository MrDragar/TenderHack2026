import argparse
import json
import os
import re
import sys
import tempfile
import zipfile
from collections import Counter
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from xml.etree import ElementTree

ACTUAL_XML_PREFIX = "nsiKTRUNew_all_actual_"
ARCHIVE_DATE_PATTERN = re.compile(r"_(\d{4}-\d{2}-\d{2})\.zip$")
CATEGORY_PREFIXES = {
    "clothing": ("14.",),
    "furniture": ("31.",),
    "tools": ("25.73.", "28.24."),
}
CHARACTERISTIC_TYPES = {"1": "text", "2": "number"}
CHARACTERISTIC_KINDS = {"1": "fixed", "2": "single", "3": "multiple"}


def local_name(name: str) -> str:
    return name.rsplit("}", 1)[-1]


def natural_key(value: str) -> list[int | str]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", value)]


def version_key(value: str | None) -> tuple[tuple[int, str], ...]:
    if not value:
        return ()
    return tuple(
        (0, f"{int(part):050d}") if part.isdigit() else (1, part)
        for part in re.split(r"(\d+)", value)
        if part
    )


def code_suffix(code: str) -> int:
    suffix = code.rsplit("-", 1)[-1]
    if not suffix.isdigit():
        raise ValueError(f"В коде КТРУ нет числового суффикса: {code}")
    return int(suffix)


def position_name_key(name: str) -> str:
    return " ".join(name.split()).casefold()


def position_rank(
    code: str,
    position_type: str,
    version: str | None,
) -> tuple[bool, int, tuple[tuple[int, str], ...], str]:
    return position_type == "final", code_suffix(code), version_key(version), code


def child(element: ElementTree.Element | None, name: str) -> ElementTree.Element | None:
    if element is None:
        return None
    return next((item for item in element if local_name(item.tag) == name), None)


def children(element: ElementTree.Element | None, name: str) -> list[ElementTree.Element]:
    if element is None:
        return []
    return [item for item in element if local_name(item.tag) == name]


def text(element: ElementTree.Element | None, name: str) -> str | None:
    item = child(element, name)
    return (item.text or "").strip() or None if item is not None else None


def parse_bool(value: str | None) -> bool:
    return value == "true"


def parse_number(value: str | None) -> int | float | None:
    if value is None:
        return None
    number = float(value)
    return int(number) if number.is_integer() else number


def parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value[:10]) if value else None


def normalize_unit(element: ElementTree.Element | None) -> dict | None:
    if element is None:
        return None
    return {"code": text(element, "code"), "name": text(element, "name")}


def normalize_range(element: ElementTree.Element | None) -> dict | None:
    value_range = child(child(element, "rangeSet"), "valueRange")
    if value_range is None:
        return None
    min_notation = text(value_range, "minMathNotation")
    max_notation = text(value_range, "maxMathNotation")
    result = {}
    minimum = parse_number(text(value_range, "min"))
    maximum = parse_number(text(value_range, "max"))
    if minimum is not None:
        result["min"] = minimum
        result["min_inclusive"] = min_notation == "greaterOrEqual"
    if maximum is not None:
        result["max"] = maximum
        result["max_inclusive"] = max_notation == "lessOrEqual"
    return result


def normalize_value(element: ElementTree.Element) -> str | int | float | dict:
    quality = text(element, "qualityDescription")
    if quality is not None:
        return quality
    value_set = child(element, "valueSet")
    number = parse_number(text(value_set, "concreteValue"))
    if number is not None:
        return number
    value_range = normalize_range(element)
    if value_range is not None:
        return value_range
    raise ValueError("У значения характеристики нет текста, числа или диапазона")


def normalize_characteristic(element: ElementTree.Element) -> dict:
    type_code = text(element, "type")
    kind_code = text(element, "kind")
    values = children(child(element, "values"), "value")
    units = {
        json.dumps(unit, ensure_ascii=False, sort_keys=True): unit
        for value in values
        if (unit := normalize_unit(child(value, "OKEI"))) is not None
    }
    if len(units) > 1:
        raise ValueError(f"У характеристики {text(element, 'name')} разные единицы")
    result = {
        "code": text(element, "code"),
        "name": text(element, "name"),
        "type": CHARACTERISTIC_TYPES.get(type_code, type_code),
        "selection": CHARACTERISTIC_KINDS.get(kind_code, kind_code),
        "required": parse_bool(text(element, "isRequired")),
        "values": [normalize_value(value) for value in values],
    }
    if units:
        result["unit"] = next(iter(units.values()))
    return result


def category_for(okpd2_code: str | None) -> str | None:
    if okpd2_code is None:
        return None
    return next(
        (
            category
            for category, prefixes in CATEGORY_PREFIXES.items()
            if okpd2_code.startswith(prefixes)
        ),
        None,
    )


def is_product(name: str | None) -> bool:
    return not (name or "").casefold().startswith("услуги по производству")


def is_applicable(data: ElementTree.Element, on_date: date) -> bool:
    if text(data, "status") != "ACTIVE":
        return False
    start = parse_date(text(data, "applicationDateStart"))
    end = parse_date(text(data, "applicationDateEnd"))
    return (start is None or start <= on_date) and (end is None or on_date <= end)


def normalize_position(data: ElementTree.Element, category: str) -> dict:
    okpd2 = child(data, "OKPD2")
    parent = child(data, "parentPositionInfo")
    okeis = child(data, "OKEIs")
    characteristics = child(data, "characteristics")
    is_template = parse_bool(text(data, "isTemplate"))
    result = {
        "code": text(data, "code"),
        "name": text(data, "name"),
        "category": category,
        "okpd2_code": text(okpd2, "code"),
        "position_type": "template" if is_template else "final",
        "parent_code": text(parent, "code"),
        "units": [normalize_unit(unit) for unit in children(okeis, "OKEI")],
        "characteristics": [
            normalize_characteristic(item)
            for item in children(characteristics, "characteristic")
            if parse_bool(text(item, "actual"))
        ],
    }
    description = text(data, "nsiDescription")
    if description is not None:
        result["description"] = description
    return result


def actual_members(archive: zipfile.ZipFile) -> list[str]:
    return sorted(
        (
            name
            for name in archive.namelist()
            if Path(name).name.startswith(ACTUAL_XML_PREFIX) and name.lower().endswith(".xml")
        ),
        key=natural_key,
    )


def sources(input_dir: Path) -> list[tuple[Path, str]]:
    result = []
    for archive_path in sorted(input_dir.glob("*.zip"), key=lambda path: natural_key(path.name)):
        with zipfile.ZipFile(archive_path) as archive:
            result.extend((archive_path, member) for member in actual_members(archive))
    return result


def source_date(source_files: list[tuple[Path, str]]) -> date:
    dates = set()
    for archive_path, _ in source_files:
        match = ARCHIVE_DATE_PATTERN.search(archive_path.name)
        if match is None:
            raise RuntimeError(f"В имени архива нет даты: {archive_path.name}")
        dates.add(date.fromisoformat(match.group(1)))
    if len(dates) != 1:
        values = ", ".join(sorted(value.isoformat() for value in dates))
        raise RuntimeError(f"В архивах смешаны даты выгрузки: {values}")
    return dates.pop()


def positions(archive_path: Path, member: str) -> Iterator[ElementTree.Element]:
    with zipfile.ZipFile(archive_path) as archive, archive.open(member) as xml:
        for _, element in ElementTree.iterparse(xml, events=("end",)):
            if local_name(element.tag) == "position":
                yield element
                element.clear()


def build(
    input_dir: Path,
    output_path: Path,
    expected_date: date | None = None,
    as_of: date | None = None,
) -> dict[str, int | str | dict]:
    source_files = sources(input_dir)
    if not source_files:
        raise RuntimeError(f"Не найдены {ACTUAL_XML_PREFIX}*.xml внутри ZIP в {input_dir}")

    data_date = source_date(source_files)
    if expected_date is not None and data_date != expected_date:
        raise RuntimeError(
            f"Ожидалась выгрузка за {expected_date.isoformat()}, найдена за {data_date.isoformat()}"
        )
    effective_date = as_of or data_date
    selected: dict[str, dict] = {}
    selected_ranks: dict[str, tuple] = {}
    input_positions = 0

    for archive_path, member in source_files:
        for position in positions(archive_path, member):
            input_positions += 1
            data = child(position, "data")
            okpd2_code = text(child(data, "OKPD2"), "code")
            category = category_for(okpd2_code)
            name = text(data, "name")
            if data is None or category is None or not is_applicable(data, effective_date):
                continue
            if name is None or not is_product(name):
                continue
            normalized = normalize_position(data, category)
            code = normalized["code"]
            name_key = position_name_key(name)
            candidate_rank = position_rank(
                code,
                normalized["position_type"],
                text(data, "version"),
            )
            if name_key not in selected or candidate_rank > selected_ranks[name_key]:
                selected[name_key] = normalized
                selected_ranks[name_key] = candidate_rank

    output_positions = sorted(
        selected.values(),
        key=lambda item: (item["category"], (item["name"] or "").casefold(), item["code"]),
    )
    category_counts = Counter(item["category"] for item in output_positions)
    metadata = {
        "source_date": data_date.isoformat(),
        "as_of": effective_date.isoformat(),
        "actual_xml_files": len(source_files),
        "input_positions": input_positions,
        "position_count": len(output_positions),
        "category_counts": dict(sorted(category_counts.items())),
    }
    payload = {"metadata": metadata, "positions": output_positions}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=output_path.parent,
        prefix=f".{output_path.stem}.",
        suffix=".json",
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        with temporary_path.open("w", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, separators=(",", ":"))
            output.write("\n")
        os.replace(temporary_path, output_path)
        output_path.chmod(0o644)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise

    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Собирает рабочий справочник КТРУ для одежды, мебели и инструментов."
    )
    parser.add_argument("--input", type=Path, default=Path("data/raw/Казань 2026/КТРУ/КТРУ"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/processed/ktru.json"),
    )
    parser.add_argument("--expected-date", type=date.fromisoformat)
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        help="Дата действия позиций; по умолчанию дата выгрузки.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        summary = build(args.input, args.output, args.expected_date, args.as_of)
    except (
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        zipfile.BadZipFile,
        ElementTree.ParseError,
    ) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1

    print(json.dumps({"output": str(args.output), **summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
