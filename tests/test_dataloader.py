import json
from datetime import date

import pytest

from scripts.dataloader import load_data, load_portal


def write_data(tmp_path):
    path = tmp_path / "ktru.json"
    payload = {
        "metadata": {"source_date": "2026-10-05"},
        "positions": [
            {"code": "14-1", "category": "clothing", "position_type": "final"},
            {"code": "31-1", "category": "furniture", "position_type": "template"},
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_data_filters_category_and_final_positions(tmp_path):
    path = write_data(tmp_path)

    assert load_data(path, category="clothing", final_only=True) == [
        {"code": "14-1", "category": "clothing", "position_type": "final"}
    ]


def test_load_data_checks_export_date(tmp_path):
    path = write_data(tmp_path)

    assert len(load_data(path, expected_date=date(2026, 10, 5))) == 2
    with pytest.raises(ValueError, match="выгрузка"):
        load_data(path, expected_date=date(2026, 10, 6))


def test_load_portal_returns_contract(tmp_path):
    path = tmp_path / "portal.json"
    payload = {
        "Костюмы": [{"name": "Цвет", "type": "Справочник", "unit": None, "values": ["Белый"]}],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert load_portal(path) == payload


def test_load_portal_rejects_broken_shape(tmp_path):
    path = tmp_path / "portal.json"
    path.write_text(json.dumps({}), encoding="utf-8")

    with pytest.raises(ValueError, match="категории"):
        load_portal(path)
