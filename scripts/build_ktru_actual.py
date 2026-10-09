import argparse
import gzip
import json
import os
import re
import sys
import tempfile
import zipfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from xml.etree import ElementTree

ACTUAL_XML_PREFIX = "nsiKTRUNew_all_actual_"
ARCHIVE_DATE_PATTERN = re.compile(r"_(\d{4}-\d{2}-\d{2})\.zip$")


def local_name(name: str) -> str:
    return name.rsplit("}", 1)[-1]


def natural_key(value: str) -> list[int | str]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", value)]


def version_key(value: str | None) -> tuple[tuple[int, int | str], ...]:
    if not value:
        return ()
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in re.split(r"(\d+)", value)
        if part
    )


def element_value(element: ElementTree.Element):
    nil = any(
        local_name(name) == "nil" and value.lower() == "true"
        for name, value in element.attrib.items()
    )
    children = list(element)
    text = (element.text or "").strip()

    if not children and not element.attrib:
        return text or None
    if nil and not children and not text:
        return None

    result = {f"@{local_name(name)}": value for name, value in element.attrib.items()}
    for child in children:
        name = local_name(child.tag)
        value = element_value(child)
        if name not in result:
            result[name] = value
        elif isinstance(result[name], list):
            result[name].append(value)
        else:
            result[name] = [result[name], value]
    if text:
        result["#text"] = text
    return result


def child_text(element: ElementTree.Element, child_name: str) -> str | None:
    for child in element:
        if local_name(child.tag) == child_name:
            return (child.text or "").strip() or None
    return None


def position_identity(position: ElementTree.Element) -> tuple[str | None, str | None]:
    for child in position:
        if local_name(child.tag) != "data":
            continue
        return child_text(child, "code"), child_text(child, "version")
    return None, None


def actual_members(archive: zipfile.ZipFile) -> list[str]:
    return sorted(
        (
            name
            for name in archive.namelist()
            if Path(name).name.startswith(ACTUAL_XML_PREFIX) and name.lower().endswith(".xml")
        ),
        key=natural_key,
    )


def sources(input_dir: Path) -> Iterator[tuple[Path, str]]:
    archives = sorted(input_dir.glob("*.zip"), key=lambda path: natural_key(path.name))
    for archive_path in archives:
        with zipfile.ZipFile(archive_path) as archive:
            for member in actual_members(archive):
                yield archive_path, member


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


def scan_latest(source_files: list[tuple[Path, str]]) -> tuple[dict[str, tuple], int]:
    latest: dict[str, tuple] = {}
    position_count = 0

    for source_number, (archive_path, member) in enumerate(source_files):
        for position_number, position in enumerate(positions(archive_path, member)):
            code, version = position_identity(position)
            identity = (source_number, position_number)
            if code is None:
                code = f"__missing_code__:{source_number}:{position_number}"
            candidate = (version_key(version), identity)
            if code not in latest or candidate > latest[code]:
                latest[code] = candidate
            position_count += 1

    return latest, position_count


def open_output(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "wt", encoding="utf-8", newline="\n")
    return path.open("w", encoding="utf-8", newline="\n")


def build(
    input_dir: Path,
    output_path: Path,
    expected_date: date | None = None,
) -> dict[str, int | str]:
    source_files = list(sources(input_dir))
    if not source_files:
        raise RuntimeError(f"Не найдены {ACTUAL_XML_PREFIX}*.xml внутри ZIP в {input_dir}")

    data_date = source_date(source_files)
    if expected_date is not None and data_date != expected_date:
        raise RuntimeError(
            f"Ожидалась выгрузка за {expected_date.isoformat()}, найдена за {data_date.isoformat()}"
        )

    latest, input_positions = scan_latest(source_files)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    suffix = "".join(output_path.suffixes) or ".tmp"
    descriptor, temporary_name = tempfile.mkstemp(
        dir=output_path.parent,
        prefix=f".{output_path.stem}.",
        suffix=suffix,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    written = 0

    try:
        with open_output(temporary_path) as output:
            for source_number, (archive_path, member) in enumerate(source_files):
                for position_number, position in enumerate(positions(archive_path, member)):
                    code, _ = position_identity(position)
                    lookup_code = code or f"__missing_code__:{source_number}:{position_number}"
                    if latest[lookup_code][1] != (source_number, position_number):
                        continue

                    record = element_value(position)
                    record["_source"] = {
                        "archive": archive_path.name,
                        "xml": member,
                    }
                    json.dump(record, output, ensure_ascii=False, separators=(",", ":"))
                    output.write("\n")
                    written += 1
        os.replace(temporary_path, output_path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise

    return {
        "data_date": data_date.isoformat(),
        "actual_xml_files": len(source_files),
        "input_positions": input_positions,
        "written_positions": written,
        "superseded_or_duplicate_positions": input_positions - written,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Объединяет актуальные выгрузки КТРУ из ZIP в один JSONL-файл."
    )
    parser.add_argument("--input", type=Path, default=Path("КТРУ_raw"))
    parser.add_argument(
        "--expected-date",
        type=date.fromisoformat,
        help="Ожидаемая дата выгрузки в формате YYYY-MM-DD.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/processed/ktru_actual.jsonl.gz"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        summary = build(args.input, args.output, args.expected_date)
    except (
        OSError,
        RuntimeError,
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
