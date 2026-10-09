import gzip
import json
from datetime import date

import pytest

from scripts.dataloader import is_applicable, iter_data


def test_is_applicable_checks_status_and_date_boundaries():
    data = {
        "status": "ACTIVE",
        "applicationDateStart": "2026-10-01T00:00:00+03:00",
        "applicationDateEnd": "2026-10-31T23:59:59+03:00",
    }

    assert is_applicable(data, date(2026, 10, 1))
    assert is_applicable(data, date(2026, 10, 31))
    assert not is_applicable(data, date(2026, 9, 30))
    assert not is_applicable({**data, "status": "TERMINATED"}, date(2026, 10, 10))


def test_iter_data_returns_data_and_checks_export_date(tmp_path):
    path = tmp_path / "ktru.jsonl.gz"
    record = {
        "data": {"code": "01.00-1", "status": "ACTIVE"},
        "_source": {"archive": "1_nsiKTRUNew_all_2026-10-05.zip"},
    }
    with gzip.open(path, "wt", encoding="utf-8") as output:
        output.write(json.dumps(record) + "\n")

    assert list(iter_data(path, expected_date=date(2026, 10, 5))) == [record["data"]]
    with pytest.raises(ValueError, match="ожидалась выгрузка"):
        list(iter_data(path, expected_date=date(2026, 10, 6)))
