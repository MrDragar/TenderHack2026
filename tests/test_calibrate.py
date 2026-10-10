from scripts.calibrate import agreement


def test_agreement_skips_ties_on_gold():
    assert agreement([0.5, 0.6, 0.9], [0.2, 0.3, 0.4]) == "3 из 3 пар, различимых на живых"
    assert agreement([0.5, 0.6, 0.9], [0.2, 0.2, 0.4]) == "2 из 2 пар, различимых на живых"
    assert agreement([0.9, 0.6], [0.2, 0.4]) == "0 из 1 пар, различимых на живых"
