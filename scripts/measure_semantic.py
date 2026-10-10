import argparse
import importlib
import json
import math
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

type Item = Mapping[str, Any] | Sequence[Any] | str
type Runner = Callable[[str, bool], Mapping[str, Any]]

METRICS = (
    "safe_success",
    "char_precision",
    "char_recall",
    "char_f1",
    "source_accuracy",
    "rejection_precision",
    "rejection_recall",
    "rejection_f0_5",
    "correction_precision",
    "correction_recall",
    "correction_f0_5",
    "question_precision",
    "question_recall",
    "write_precision",
    "write_recall",
    "garbage_write_rate",
    "reuse_recall",
    "mode_accuracy",
    "candidate_recall_1",
    "candidate_recall_5",
    "candidate_recall_20",
    "candidate_mrr",
    "required_question_recall",
    "corrected_text_accuracy",
    "extra_question_rate",
    "clean_overcorrection_rate",
    "duplicate_write_rate",
    "precedence_violation_rate",
    "wrong_scope_leakage_rate",
    "p50",
    "p95",
)


def measure_scenarios(scenarios: Iterable[Mapping[str, Any]], runner: Runner) -> dict[str, float]:
    counts = defaultdict(int)
    latencies: list[float] = []

    for scenario in scenarios:
        begin = getattr(runner, "begin_scenario", None)
        if begin:
            begin(scenario)
        scenario_safe = True
        has_turns = False
        for turn in scenario.get("turns", []):
            if turn.get("restart"):
                restart = getattr(runner, "restart", None)
                if restart:
                    restart()
            has_turns = True
            counts["turns"] += 1
            started = time.perf_counter()
            result = runner(str(turn["text"]), bool(turn.get("learn", True)))
            latencies.append(time.perf_counter() - started)
            expected = turn.get("expected", {})

            expected_chars = list(expected.get("chars", []))
            actual_chars = result_items(result, "chars")
            tp, fp, fn = matches(expected_chars, actual_chars, char_key)
            add_counts(counts, "char", tp, fp, fn)

            matched, correct_source = source_matches(expected_chars, actual_chars)
            counts["source_total"] += matched
            counts["source_ok"] += correct_source

            expected_rejected = list(expected.get("rejected", []))
            actual_rejected = result_items(result, "rejected")
            tp, fp, fn = matches(expected_rejected, actual_rejected, rejected_key)
            add_counts(counts, "rejection", tp, fp, fn)

            expected_corrections = list(expected.get("corrections", []))
            actual_corrections = result_items(result, "corrections")
            tp, fp, fn = matches(expected_corrections, actual_corrections, correction_key)
            add_counts(counts, "correction", tp, fp, fn)

            expected_questions = list(expected.get("questions", []))
            actual_questions = result_items(result, "questions")
            tp, fp, fn = matches(expected_questions, actual_questions, question_key)
            add_counts(counts, "question", tp, fp, fn)
            required_expected = [
                item for item in expected_questions if question_key(item)[1] == "required"
            ]
            required_actual = [
                item for item in actual_questions if question_key(item)[1] == "required"
            ]
            required_tp, _, required_fn = matches(required_expected, required_actual, question_key)
            counts["required_question_tp"] += required_tp
            counts["required_question_fn"] += required_fn
            counts["extra_questions"] += max(0, len(actual_questions) - len(expected_questions))
            counts["question_total"] += max(1, len(actual_questions))

            expected_writes = list(expected.get("writes", []))
            actual_writes = result_items(result, "writes")
            tp, fp, fn = matches(expected_writes, actual_writes, write_key)
            add_counts(counts, "write", tp, fp, fn)

            expected_garbage = Counter(rejected_name(item) for item in expected_rejected)
            actual_write_names = Counter(write_name(item) for item in actual_writes)
            counts["garbage"] += sum((expected_garbage & actual_write_names).values())
            counts["garbage_total"] += sum(expected_garbage.values())

            expected_reused = [item for item in expected_chars if char_source(item) == "learned"]
            actual_learned = [item for item in actual_chars if char_source(item) == "learned"]
            reused, _, _ = matches(expected_reused, actual_learned, char_key)
            counts["reuse_ok"] += reused
            counts["reuse_total"] += len(expected_reused)

            expected_new = [item for item in expected_chars if char_source(item) == "new"]
            actual_by_key = {char_key(item): char_source(item) for item in actual_chars}
            counts["scope_leaks"] += sum(
                actual_by_key.get(char_key(item)) == "learned" for item in expected_new
            )
            counts["scope_leak_total"] += len(expected_new)

            write_keys = [write_key(item) for item in actual_writes]
            counts["duplicate_writes"] += len(write_keys) - len(set(write_keys))
            counts["write_total"] += len(write_keys)

            names = [char_key(item)[0] for item in actual_chars]
            counts["precedence_violations"] += len(names) != len(set(names))
            counts["precedence_total"] += 1

            actual_corrections = result_items(result, "corrections")
            if not expected_corrections:
                counts["clean_turns"] += 1
                counts["overcorrected"] += bool(actual_corrections)

            if "corrected_text" in expected:
                counts["corrected_text_total"] += 1
                counts["corrected_text_ok"] += normalize(expected["corrected_text"]) == normalize(
                    result.get("corrected_text")
                )

            if expected.get("code"):
                counts["candidate_total"] += 1
                candidate_codes = [
                    item.get("code")
                    for item in result.get("candidates", [])
                    if isinstance(item, Mapping)
                ]
                try:
                    candidate_rank = candidate_codes.index(expected["code"]) + 1
                except ValueError:
                    candidate_rank = None
                for cutoff in (1, 5, 20):
                    counts[f"candidate_recall_{cutoff}"] += bool(
                        candidate_rank and candidate_rank <= cutoff
                    )
                if candidate_rank:
                    counts["candidate_rr_million"] += round(1_000_000 / candidate_rank)

            if "mode" in expected:
                counts["mode_total"] += 1
                counts["mode_ok"] += normalize(result_mode(result)) == normalize(expected["mode"])

            if not safe_turn(expected, result):
                scenario_safe = False

        if has_turns:
            counts["scenarios"] += 1
            counts["safe"] += scenario_safe
        end = getattr(runner, "end_scenario", None)
        if end:
            end()

    if counts["turns"] == 0:
        return {name: 0.0 for name in METRICS}

    char_precision, char_recall = precision_recall(counts, "char")
    rejection_precision, rejection_recall = precision_recall(counts, "rejection")
    correction_precision, correction_recall = precision_recall(counts, "correction")
    question_precision, question_recall = precision_recall(counts, "question")
    write_precision, write_recall = precision_recall(counts, "write")
    return {
        "safe_success": divide(counts["safe"], counts["scenarios"]),
        "char_precision": char_precision,
        "char_recall": char_recall,
        "char_f1": f_beta(char_precision, char_recall, 1),
        "source_accuracy": divide(counts["source_ok"], counts["source_total"]),
        "rejection_precision": rejection_precision,
        "rejection_recall": rejection_recall,
        "rejection_f0_5": f_beta(rejection_precision, rejection_recall, 0.5),
        "correction_precision": correction_precision,
        "correction_recall": correction_recall,
        "correction_f0_5": f_beta(correction_precision, correction_recall, 0.5),
        "question_precision": question_precision,
        "question_recall": question_recall,
        "write_precision": write_precision,
        "write_recall": write_recall,
        "garbage_write_rate": zero_divide(counts["garbage"], counts["garbage_total"]),
        "reuse_recall": divide(counts["reuse_ok"], counts["reuse_total"]),
        "mode_accuracy": divide(counts["mode_ok"], counts["mode_total"]),
        "candidate_recall_1": divide(counts["candidate_recall_1"], counts["candidate_total"]),
        "candidate_recall_5": divide(counts["candidate_recall_5"], counts["candidate_total"]),
        "candidate_recall_20": divide(counts["candidate_recall_20"], counts["candidate_total"]),
        "candidate_mrr": (
            counts["candidate_rr_million"] / counts["candidate_total"] / 1_000_000
            if counts["candidate_total"]
            else 1.0
        ),
        "required_question_recall": divide(
            counts["required_question_tp"],
            counts["required_question_tp"] + counts["required_question_fn"],
        ),
        "corrected_text_accuracy": divide(
            counts["corrected_text_ok"], counts["corrected_text_total"]
        ),
        "extra_question_rate": zero_divide(counts["extra_questions"], counts["question_total"]),
        "clean_overcorrection_rate": zero_divide(counts["overcorrected"], counts["clean_turns"]),
        "duplicate_write_rate": zero_divide(counts["duplicate_writes"], counts["write_total"]),
        "precedence_violation_rate": zero_divide(
            counts["precedence_violations"], counts["precedence_total"]
        ),
        "wrong_scope_leakage_rate": zero_divide(counts["scope_leaks"], counts["scope_leak_total"]),
        "p50": percentile(latencies, 0.5),
        "p95": percentile(latencies, 0.95),
    }


def result_items(result: Mapping[str, Any], name: str) -> list[Item]:
    aliases = {
        "chars": ("chars", "characteristics"),
        "rejected": ("rejected", "garbage"),
        "corrections": ("corrections", "edits", "remarks"),
    }
    for key in aliases.get(name, (name,)):
        if key in result:
            value = result[key]
            return list(value) if value else []

    if name == "questions":
        found = []
        if result.get("question"):
            found.append(result["question"])
        known = {question_name(item) for item in found}
        for char in result.get("missing_required", []):
            if normalize(char) not in known:
                found.append({"char": char, "kind": "required"})
        return found

    if name == "writes":
        for key in ("learning_delta", "learning"):
            learning = result.get(key)
            if isinstance(learning, Mapping):
                value = learning.get("created", learning.get("writes", []))
                return list(value) if value else []
            if isinstance(learning, Sequence) and not isinstance(learning, str | bytes):
                return [
                    item
                    for item in learning
                    if isinstance(item, Mapping) and item.get("action") in {"created", "updated"}
                ]
    return []


def safe_turn(expected: Mapping[str, Any], result: Mapping[str, Any]) -> bool:
    list_fields = {
        "chars": (char_full_key, result_items(result, "chars")),
        "rejected": (rejected_key, result_items(result, "rejected")),
        "corrections": (correction_key, result_items(result, "corrections")),
        "questions": (question_key, result_items(result, "questions")),
        "writes": (write_key, result_items(result, "writes")),
    }
    for name, (key, actual) in list_fields.items():
        if name in expected and Counter(map(key, expected[name])) != Counter(map(key, actual)):
            return False

    if "mode" in expected and normalize(expected["mode"]) != normalize(result_mode(result)):
        return False

    ignored = {*list_fields, "mode"}
    for name, value in expected.items():
        if name not in ignored and normalize(value) != normalize(result.get(name)):
            return False
    return True


def result_mode(result: Mapping[str, Any]) -> Any:
    if "mode" in result:
        return result["mode"]
    if "status" in result:
        return result["status"]
    if "catalog_status" in result:
        return result["catalog_status"]
    if result.get("code"):
        return "ktru"
    if result.get("portal_category"):
        return "portal_only"
    return "not_found"


def source_matches(expected: list[Item], actual: list[Item]) -> tuple[int, int]:
    expected_by_char: dict[Any, Counter] = defaultdict(Counter)
    actual_by_char: dict[Any, Counter] = defaultdict(Counter)
    for item in expected:
        expected_by_char[char_key(item)][char_source(item)] += 1
    for item in actual:
        actual_by_char[char_key(item)][char_source(item)] += 1

    matched = 0
    correct = 0
    for key, expected_sources in expected_by_char.items():
        actual_sources = actual_by_char[key]
        matched += min(sum(expected_sources.values()), sum(actual_sources.values()))
        correct += sum((expected_sources & actual_sources).values())
    return matched, correct


def matches(
    expected: list[Item], actual: list[Item], key: Callable[[Item], Any]
) -> tuple[int, int, int]:
    expected_counts = Counter(map(key, expected))
    actual_counts = Counter(map(key, actual))
    tp = sum((expected_counts & actual_counts).values())
    return tp, sum(actual_counts.values()) - tp, sum(expected_counts.values()) - tp


def add_counts(counts: defaultdict[str, int], name: str, tp: int, fp: int, fn: int) -> None:
    counts[f"{name}_tp"] += tp
    counts[f"{name}_fp"] += fp
    counts[f"{name}_fn"] += fn


def precision_recall(counts: defaultdict[str, int], name: str) -> tuple[float, float]:
    tp = counts[f"{name}_tp"]
    return divide(tp, tp + counts[f"{name}_fp"]), divide(tp, tp + counts[f"{name}_fn"])


def divide(part: int, total: int) -> float:
    return part / total if total else 1.0


def zero_divide(part: int, total: int) -> float:
    return part / total if total else 0.0


def f_beta(precision: float, recall: float, beta: float) -> float:
    beta_squared = beta**2
    denominator = beta_squared * precision + recall
    if denominator == 0:
        return 0.0
    return (1 + beta_squared) * precision * recall / denominator


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    low = math.floor(index)
    high = math.ceil(index)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def char_key(item: Item) -> tuple[Any, Any, Any]:
    if isinstance(item, Mapping):
        return normalize(item.get("name")), normalize(item.get("value")), unit(item.get("unit"))
    values = sequence(item, 4)
    return normalize(values[0]), normalize(values[1]), unit(values[2])


def char_source(item: Item) -> Any:
    if isinstance(item, Mapping):
        return normalize(item.get("source"))
    return normalize(sequence(item, 4)[3])


def char_full_key(item: Item) -> tuple[Any, Any, Any, Any]:
    return *char_key(item), char_source(item)


def rejected_key(item: Item) -> tuple[Any, Any]:
    if isinstance(item, Mapping):
        name = item.get("name", item.get("fragment"))
        value = item.get("value")
        if value is None and isinstance(name, str) and ":" in name:
            name, value = name.split(":", 1)
        return normalize(name), normalize(value)
    if isinstance(item, str):
        if ":" in item:
            name, value = item.split(":", 1)
            return normalize(name), normalize(value)
        return normalize(item), None
    values = sequence(item, 2)
    return normalize(values[0]), normalize(values[1])


def rejected_name(item: Item) -> Any:
    return rejected_key(item)[0]


def correction_key(item: Item) -> tuple[Any, Any, Any]:
    if isinstance(item, Mapping):
        was = item.get("was", item.get("fragment"))
        now = item.get("now", item.get("replacement"))
        return normalize(was), normalize(now), normalize(item.get("kind"))
    values = sequence(item, 3)
    return normalize(values[0]), normalize(values[1]), normalize(values[2])


def question_key(item: Item) -> tuple[Any, Any]:
    if isinstance(item, Mapping):
        return normalize(item.get("char", item.get("name"))), normalize(item.get("kind"))
    if isinstance(item, str):
        return normalize(item), None
    values = sequence(item, 2)
    return normalize(values[0]), normalize(values[1])


def question_name(item: Item) -> Any:
    return question_key(item)[0]


def write_key(item: Item) -> tuple[Any, Any]:
    if isinstance(item, Mapping):
        return normalize(item.get("scope", item.get("context"))), normalize(item.get("name"))
    if isinstance(item, str):
        return None, normalize(item)
    values = sequence(item, 2)
    return normalize(values[0]), normalize(values[1])


def write_name(item: Item) -> Any:
    return write_key(item)[1]


def unit(value: Any) -> Any:
    if isinstance(value, Mapping):
        return normalize(value.get("code", value.get("name")))
    return normalize(value)


def sequence(item: Item, size: int) -> list[Any]:
    if isinstance(item, str) or not isinstance(item, Sequence) or len(item) != size:
        raise ValueError(f"Ожидался список из {size} элементов, получено {item!r}")
    return list(item)


def normalize(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(value.split()).casefold()
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    if isinstance(value, Mapping):
        return tuple(sorted((normalize(key), normalize(item)) for key, item in value.items()))
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
        return tuple(normalize(item) for item in value)
    return value


def load_runner(path: str) -> Runner:
    module_name, separator, name = path.partition(":")
    if not separator:
        raise ValueError("Runner задаётся как package.module:function")
    return getattr(importlib.import_module(module_name), name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Замер семантического пайплайна")
    parser.add_argument("scenarios", type=Path)
    parser.add_argument("runner", help="Python callable в формате package.module:function")
    args = parser.parse_args()
    with args.scenarios.open(encoding="utf-8") as source:
        payload = json.load(source)
    scenarios = payload.get("scenarios", []) if isinstance(payload, dict) else payload
    print(json.dumps(measure_scenarios(scenarios, load_runner(args.runner)), ensure_ascii=False))


if __name__ == "__main__":
    main()
