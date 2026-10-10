from collections.abc import Mapping
from typing import Any

import pytest

import scripts.measure_semantic as measure
from scripts.measure_semantic import METRICS, measure_scenarios


def char(name, value, source, unit=None):
    return [name, value, unit, source]


def result(**values):
    found = {
        "mode": "ktru",
        "chars": [],
        "rejected": [],
        "corrections": [],
        "questions": [],
        "writes": [],
        **values,
    }
    if "characteristics" in values:
        found.pop("chars")
    if "learning_delta" in values:
        found.pop("writes")
    return found


def expected(**values):
    return {
        "mode": "ktru",
        "chars": [],
        "rejected": [],
        "corrections": [],
        "questions": [],
        "writes": [],
        **values,
    }


class FakeRunner:
    def __init__(self, answers: Mapping[str, dict[str, Any]]):
        self.answers = answers
        self.calls = []

    def __call__(self, text: str, learn: bool):
        self.calls.append((text, learn))
        return self.answers[text]


def test_perfect_learning_scenario_has_perfect_metrics():
    scenarios = [
        {
            "turns": [
                {
                    "text": "Стол, кромка: АБС, вкус: клубничный",
                    "learn": True,
                    "expected": expected(
                        chars=[char("Кромка столешницы", "АБС", "new")],
                        rejected=[["Вкус", "клубничный"]],
                        corrections=[["кромка", "Кромка столешницы", "typo"]],
                        questions=[["Ширина", "required"]],
                        writes=[["ktru:desk", "Кромка столешницы"]],
                    ),
                },
                {
                    "text": "Стол, кромка: ПВХ",
                    "learn": False,
                    "expected": expected(chars=[char("Кромка столешницы", "ПВХ", "learned")]),
                },
            ]
        }
    ]
    runner = FakeRunner(
        {
            "Стол, кромка: АБС, вкус: клубничный": result(
                chars=[
                    {
                        "name": "кромка столешницы",
                        "value": "абс",
                        "unit": None,
                        "source": "NEW",
                    }
                ],
                rejected=[{"name": "Вкус", "value": "Клубничный"}],
                corrections=[{"was": "КРОМКА", "now": "кромка столешницы", "kind": "typo"}],
                questions=[{"char": "Ширина", "kind": "required"}],
                learning_delta={"created": [{"scope": "KTRU:DESK", "name": "Кромка столешницы"}]},
            ),
            "Стол, кромка: ПВХ": result(
                characteristics=[
                    {
                        "name": "Кромка столешницы",
                        "value": "ПВХ",
                        "unit": None,
                        "source": "learned",
                    }
                ]
            ),
        }
    )

    metrics = measure_scenarios(scenarios, runner)

    assert runner.calls == [
        ("Стол, кромка: АБС, вкус: клубничный", True),
        ("Стол, кромка: ПВХ", False),
    ]
    assert set(metrics) == set(METRICS)
    zero_is_best = {
        "garbage_write_rate",
        "extra_question_rate",
        "clean_overcorrection_rate",
        "duplicate_write_rate",
        "precedence_violation_rate",
        "wrong_scope_leakage_rate",
    }
    for name, value in metrics.items():
        if name not in {*zero_is_best, "p50", "p95"}:
            assert value == 1, name
    assert all(metrics[name] == 0 for name in zero_is_best)
    assert 0 <= metrics["p50"] <= metrics["p95"]


def test_metrics_count_false_positive_false_negative_and_garbage_write():
    wanted = expected(
        mode="portal_only",
        chars=[
            char("Материал", "Сталь", "ktru"),
            char("Кромка", "ПВХ", "learned"),
        ],
        rejected=[["Вкус", "клубничный"], ["Скидка", "20%"]],
        corrections=[
            ["металический", "металлический", "typo"],
            ["0,75 м", "750 мм", "unit"],
        ],
        questions=[["Ширина", "required"], ["Тип каркаса", "disambiguation"]],
        writes=[["desk", "Кромка"], ["desk", "Форма кромки"]],
    )
    actual = result(
        chars=[
            {"name": "Материал", "value": "Сталь", "unit": None, "source": "portal"},
            {"name": "Цвет", "value": "Серый", "unit": None, "source": "portal"},
        ],
        rejected=[{"name": "Вкус", "value": "клубничный"}],
        corrections=[
            {"was": "металический", "now": "металлический", "kind": "typo"},
            {"was": "серый", "now": "Серый", "kind": "typo"},
        ],
        questions=[{"char": "Ширина", "kind": "required"}],
        writes=[
            {"scope": "desk", "name": "Кромка"},
            {"scope": "desk", "name": "Скидка"},
        ],
    )
    metrics = measure_scenarios(
        [{"turns": [{"text": "bad", "expected": wanted}]}], FakeRunner({"bad": actual})
    )

    assert metrics["safe_success"] == 0
    assert metrics["char_precision"] == 0.5
    assert metrics["char_recall"] == 0.5
    assert metrics["char_f1"] == 0.5
    assert metrics["source_accuracy"] == 0
    assert metrics["rejection_precision"] == 1
    assert metrics["rejection_recall"] == 0.5
    assert metrics["rejection_f0_5"] == pytest.approx(5 / 6)
    assert metrics["correction_precision"] == 0.5
    assert metrics["correction_recall"] == 0.5
    assert metrics["correction_f0_5"] == 0.5
    assert metrics["question_precision"] == 1
    assert metrics["question_recall"] == 0.5
    assert metrics["write_precision"] == 0.5
    assert metrics["write_recall"] == 0.5
    assert metrics["garbage_write_rate"] == 0.5
    assert metrics["reuse_recall"] == 0
    assert metrics["mode_accuracy"] == 0


def test_safe_success_requires_every_turn_of_scenario_to_be_right():
    good = result()
    bad = result(mode="not_found")
    scenarios = [
        {
            "turns": [
                {"text": "one", "expected": expected()},
                {"text": "two", "expected": expected()},
            ]
        },
        {"turns": [{"text": "three", "expected": expected()}]},
    ]

    metrics = measure_scenarios(scenarios, FakeRunner({"one": good, "two": bad, "three": good}))

    assert metrics["safe_success"] == 0.5
    assert metrics["mode_accuracy"] == pytest.approx(2 / 3)


def test_units_use_code_and_numeric_values_ignore_float_tail():
    scenarios = [
        {
            "turns": [
                {
                    "text": "Размер 48",
                    "expected": expected(chars=[char("Размер", "48", "ktru", "003")]),
                }
            ]
        }
    ]
    actual = result(
        chars=[
            {
                "name": "размер",
                "value": 48.0,
                "unit": {"code": "003", "name": "Миллиметр"},
                "source": "ktru",
            }
        ]
    )

    metrics = measure_scenarios(scenarios, FakeRunner({"Размер 48": actual}))

    assert metrics["char_f1"] == 1
    assert metrics["safe_success"] == 1


def test_understands_status_edits_and_created_learning_decisions():
    scenarios = [
        {
            "turns": [
                {
                    "text": "Стол",
                    "expected": expected(
                        mode="ready",
                        corrections=[["cnjk", "стол", "layout"]],
                        writes=[["31.01.10", "Кромка"]],
                    ),
                }
            ]
        }
    ]
    actual = {
        "status": "ready",
        "chars": [],
        "rejected": [],
        "questions": [],
        "edits": [{"was": "cnjk", "now": "стол", "kind": "layout"}],
        "learning": [
            {"name": "Кромка", "action": "created", "context": "31.01.10"},
            {"name": "Цвет", "action": "reused", "context": "31.01.10"},
        ],
    }

    metrics = measure_scenarios(scenarios, FakeRunner({"Стол": actual}))

    assert metrics["mode_accuracy"] == 1
    assert metrics["correction_f0_5"] == 1
    assert metrics["write_precision"] == 1
    assert metrics["write_recall"] == 1
    assert metrics["safe_success"] == 1


def test_latency_uses_linear_percentiles(monkeypatch):
    clock = iter([0.0, 0.01, 1.0, 1.02, 2.0, 2.03, 3.0, 3.04])
    monkeypatch.setattr(measure.time, "perf_counter", lambda: next(clock))
    scenarios = [
        {
            "turns": [
                {"text": name, "expected": expected()} for name in ["one", "two", "three", "four"]
            ]
        }
    ]
    runner = FakeRunner({name: result() for name in ["one", "two", "three", "four"]})

    metrics = measure_scenarios(scenarios, runner)

    assert metrics["p50"] == pytest.approx(0.025)
    assert metrics["p95"] == pytest.approx(0.0385)


def test_empty_scenarios_return_zeroes_without_calling_runner():
    runner = FakeRunner({})

    metrics = measure_scenarios([], runner)

    assert metrics == {name: 0.0 for name in METRICS}
    assert runner.calls == []


def test_runner_gets_isolated_scenario_lifecycle_and_restart():
    class LifecycleRunner(FakeRunner):
        def __init__(self):
            super().__init__({"one": result(), "two": result()})
            self.events = []

        def begin_scenario(self, scenario):
            self.events.append(("begin", scenario["id"]))

        def restart(self):
            self.events.append(("restart", None))

        def end_scenario(self):
            self.events.append(("end", None))

    runner = LifecycleRunner()
    scenarios = [
        {
            "id": "first",
            "turns": [
                {"text": "one", "expected": expected()},
                {"text": "two", "restart": True, "expected": expected()},
            ],
        }
    ]

    measure_scenarios(scenarios, runner)

    assert runner.events == [("begin", "first"), ("restart", None), ("end", None)]
