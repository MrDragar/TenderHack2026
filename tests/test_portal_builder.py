import json
from datetime import datetime, time

from openpyxl import Workbook

from scripts.build_portal import (
    EXPECTED_HEADERS,
    SOURCE_FILES,
    build,
    clean_values,
    workbook_rows,
)


def write_workbook(path, rows):
    workbook = Workbook()
    workbook.active.title = "Лист1"
    sheet = workbook.create_sheet("Лист2")
    header = [f"Колонка {index}" for index in range(8)]
    for index, name in EXPECTED_HEADERS.items():
        header[index] = name
    sheet.append(header)
    for row in rows:
        sheet.append(row)
    workbook.save(path)


def test_build_stitches_rows_cleans_values_and_aggregates_categories(tmp_path):
    common = [
        [
            "Категория",
            "01",
            "Название",
            "Цвет",
            "Базовая",
            "Справочник",
            "Белый; [не найдено значение ID: 1]; Кра",
            None,
        ],
        ["сный", None, "Сдвинутый ID", "Цвет | Цвет", "Сдвинутое значение"],
        [
            "Категория",
            "01",
            "Название",
            "Цвет",
            "Базовая",
            "Текст",
            "белый; [не найдено значение ID: 2; Синий\tлишняя строка_x000D_",
            None,
        ],
        ["Категория", "01", "Название", "Цвет | Цвет", None, None, None, None],
    ]
    for filename in SOURCE_FILES:
        write_workbook(tmp_path / filename, common)
    output_path = tmp_path / "portal.json"

    portal = build(tmp_path, output_path)

    expected = {
        "name": "Цвет",
        "type": "Справочник",
        "unit": None,
        "values": ["Белый", "Красный", "Синий"],
    }
    assert portal == {"Категория": [expected]}
    assert json.loads(output_path.read_text(encoding="utf-8")) == portal
    assert "не найдено значение ID" not in output_path.read_text(encoding="utf-8")


def test_build_requires_all_three_workbooks(tmp_path):
    write_workbook(tmp_path / SOURCE_FILES[0], [])

    try:
        build(tmp_path, tmp_path / "portal.json")
    except FileNotFoundError as error:
        assert "Мебель.xlsx" in str(error)
        assert "Инструменты.xlsx" in str(error)
    else:
        raise AssertionError("Сборка должна требовать все три XLSX")


def test_workbook_rows_recovers_formula_and_time_but_skips_excel_date(tmp_path):
    path = tmp_path / "Источник.xlsx"
    write_workbook(
        path,
        [
            ["Категория", "01", "Название", "Температура", "Базовая", "Текст", "=-10 - 55"],
            [
                "Категория",
                "01",
                "Название",
                "Размер",
                "Базовая",
                "Текст",
                datetime(2026, 4, 1),
            ],
            [
                "Категория",
                "01",
                "Название",
                "Передаточное число",
                "Базовая",
                "Текст",
                time(1, 58),
            ],
        ],
    )

    rows = list(workbook_rows(path))

    assert rows[0][6] == "-10 - 55"
    assert rows[1][6] is None
    assert rows[2][6] == "1:58"


def test_clean_values_decodes_html_before_splitting():
    assert list(clean_values("&lt; 10; =20; [не найдено значение ID: 42]")) == ["< 10", "20"]
