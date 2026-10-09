from pathlib import Path

import pytest

from contracts.ref import Characteristic, Position
from scripts.dataloader import DEFAULT_DATA_PATH, load_data


def fits(item, contract):
    allowed = contract.__required_keys__ | contract.__optional_keys__
    return contract.__required_keys__ <= set(item) <= allowed


@pytest.mark.skipif(not Path(DEFAULT_DATA_PATH).exists(), reason="справочник не собран")
def test_built_reference_matches_contract():
    positions = load_data()

    assert positions
    for position in positions:
        assert fits(position, Position), position["code"]
        for characteristic in position["characteristics"]:
            assert fits(characteristic, Characteristic), position["code"]
