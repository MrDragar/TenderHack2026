from contracts.pipeline import Decision
from pipeline.trash import (
    NOT_PROPERTY,
    PROPERTY_ABOVE,
    TRASH_BELOW,
    decide,
    decisions,
    property_scores,
    verdicts,
)

HANDLE: Decision = {
    "told": "Ручка противоскользящая",
    "name": "Наличие противоскользящей ручки",
    "product": "Молоток",
    "approved": True,
}
DEAL: Decision = {"told": "Торг", "name": "Торг", "product": "Молоток", "approved": False}


def model(score: float):
    """Модель с одной и той же вероятностью свойства на любой кусок."""

    def entail(pairs: list[tuple[str, str]]) -> list[float]:
        return [1 - score if h == NOT_PROPERTY else score for _, h in pairs]

    return entail


def never(pairs: list[tuple[str, str]]) -> list[float]:
    raise AssertionError("модель не должна вызываться")


def test_confident_model_gives_property_and_trash():
    high = verdicts(["Ручка противоскользящая: да"], "Молоток", [], model(0.95))[0]
    low = verdicts(["продам срочно"], "Молоток", [], model(0.02))[0]

    assert high["verdict"] == "property"
    assert high["score"] is not None and high["score"] > PROPERTY_ABOVE
    assert low["verdict"] == "trash"
    assert low["score"] is not None and low["score"] < TRASH_BELOW


def test_doubtful_goes_to_moderator():
    assert verdicts(["узор"], "Молоток", [], model(0.5))[0]["verdict"] == "unsure"


def test_without_model_everything_goes_to_moderator():
    found = verdicts(["Ручка противоскользящая: да", "узор"], "Молоток", [], None)

    assert [c["verdict"] for c in found] == ["unsure", "unsure"]
    assert all(c["score"] is None for c in found)


def test_memory_wins_over_model_and_gives_unified_name():
    found = verdicts(["ручки противоскользящие: есть", "торг"], "Молоток", [HANDLE, DEAL], never)

    assert [c["verdict"] for c in found] == ["property", "trash"]
    assert found[0]["name"] == "Наличие противоскользящей ручки"
    assert found[1]["name"] is None


def test_later_decision_wins():
    again = DEAL | {"approved": True}

    assert verdicts(["Торг"], "Молоток", [DEAL, again], never)[0]["verdict"] == "property"


def test_contacts_are_trash_without_model():
    told = ["Телефон: +7 (999) 111-22-33", "https://avito.ru/1", "почта: shop@mail.ru"]

    assert [c["verdict"] for c in verdicts(told, "Молоток", [], never)] == ["trash"] * 3


def test_doubtful_follows_similar_past_decision():
    found = verdicts(["Ручка прорезиненная: да"], "Молоток", [HANDLE], model(0.5))[0]

    assert found["verdict"] == "property"
    assert found["name"] is None
    assert "Наличие противоскользящей ручки" in found["why"]
    assert found["score"] == 0.5


def test_confident_model_ignores_similar_decision():
    found = verdicts(["Ручка прорезиненная: да"], "Молоток", [HANDLE], model(0.01))[0]

    assert found["verdict"] == "trash"


def test_empty_and_punctuation_only():
    assert verdicts([], "Молоток", [HANDLE], never) == []
    assert verdicts([":"], "Молоток", [HANDLE], model(0.5))[0]["verdict"] == "unsure"


def test_scores_keep_order_and_survive_zero_probabilities():
    scores = property_scores([("Молоток", "а"), ("Стол", "б")], lambda pairs: [0.0] * len(pairs))

    assert scores == [0.5, 0.5]


def test_decide_appends_and_reads_back(tmp_path):
    path = tmp_path / "moderation.json"
    assert decisions(path) == []

    decide(HANDLE, path)
    decide(DEAL, path)

    assert decisions(path) == [HANDLE, DEAL]
