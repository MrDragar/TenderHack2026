from datetime import date
from xml.etree import ElementTree

from scripts.build_ktru_actual import (
    category_for,
    is_applicable,
    normalize_characteristic,
    position_rank,
)


def xml(value):
    return ElementTree.fromstring(value)


def test_category_for_only_includes_hackathon_categories():
    assert category_for("14.13.21.120") == "clothing"
    assert category_for("31.01.11.150") == "furniture"
    assert category_for("25.73.30.141") == "tools"
    assert category_for("28.24.11.000") == "tools"
    assert category_for("32.50.13.190") is None


def test_is_applicable_checks_status_and_date_boundaries():
    data = xml(
        """
        <data>
          <status>ACTIVE</status>
          <applicationDateStart>2026-10-01T00:00:00+03:00</applicationDateStart>
          <applicationDateEnd>2026-10-31T23:59:59+03:00</applicationDateEnd>
        </data>
        """
    )

    assert is_applicable(data, date(2026, 10, 1))
    assert is_applicable(data, date(2026, 10, 31))
    assert not is_applicable(data, date(2026, 11, 1))


def test_normalize_characteristic_produces_numeric_range():
    characteristic = xml(
        """
        <characteristic>
          <code>length</code>
          <name>Длина рукоятки</name>
          <type>2</type>
          <kind>1</kind>
          <isRequired>true</isRequired>
          <values>
            <value>
              <valueCode>range-1</valueCode>
              <OKEI><code>003</code><name>Миллиметр</name></OKEI>
              <rangeSet><valueRange>
                <minMathNotation>greaterOrEqual</minMathNotation><min>100</min>
                <maxMathNotation>less</maxMathNotation><max>150</max>
              </valueRange></rangeSet>
            </value>
          </values>
        </characteristic>
        """
    )

    result = normalize_characteristic(characteristic)

    assert result["type"] == "number"
    assert result["selection"] == "fixed"
    assert result["required"] is True
    assert result["unit"] == {"code": "003", "name": "Миллиметр"}
    assert result["values"][0] == {
        "min": 100,
        "min_inclusive": True,
        "max": 150,
        "max_inclusive": False,
    }


def test_newest_version_of_a_code_wins():
    old = position_rank("final", "100")
    latest = position_rank("final", "101")
    parent = position_rank("template", "999")

    assert latest > old
    assert latest > parent
