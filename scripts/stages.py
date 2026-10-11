import json
import sys
from pathlib import Path
from random import Random
from typing import TypedDict

from contracts.pipeline import Candidate, FindCandidates, FoundChar, Fragment, Narrowed
from contracts.ref import Portal, Position
from pipeline.candidates import find
from pipeline.extract import NUMBER, extract
from pipeline.link import link
from pipeline.narrow import narrow
from pipeline.run import solve
from pipeline.section import find_section
from pipeline.values import same, show
from scripts.generate import (
    Generated,
    generate,
    gold_char,
    inside,
    section_texts,
    siblings,
)
from scripts.products import Product, products

LAST_DIR = Path("data/eval/last")
SHOWN_NEW = 5
GENERATED = 300
SECTIONS = 300
LAID_OUT = 50
CATEGORY_NAMES = {"clothing": "одежда", "furniture": "мебель", "tools": "инструменты"}
LEVELS = {"ktru": "КТРУ", "portal": "портал", "new": "новые"}
# Пара это доля: верных и всего.
type Number = int | float | tuple[int, int]


class Failure(TypedDict):
    text: str
    expected: str
    got: str


class Stage(TypedDict):
    key: str
    title: str
    metrics: dict[str, Number]
    failures: list[Failure]


def share(part: int, total: int) -> str:
    return f"{part}/{total} ({part / total:.0%})" if total else "0/0"


def stage1(generated: list[Generated], ref: list[Position]) -> Stage:
    """Соседние коды одного шаблона называются одинаково, их различает сужение, а не этап 1.
    Поэтому ранг считаем по названиям шаблонов: у шести шаблонов КТРУ название общее."""
    by_code = {p["code"]: p for p in ref}
    metrics: dict[str, Number] = {}
    failures: list[Failure] = []
    right: dict[int, bool] = {}
    for label, part in split(generated):
        top1 = 0
        top5 = 0
        reciprocal = 0.0
        category = 0
        sizes = 0
        for example in part:
            candidates = find(example["text"], ref)
            sizes += len(candidates)
            found = template_names(candidates, by_code)
            gold = named(template_of(example["code"], by_code), by_code)
            place = found.index(gold) + 1 if gold in found else None
            right[id(example)] = place == 1
            if place is not None:
                top1 += place == 1
                top5 += place <= 5
                reciprocal += 1 / place
            if place != 1:
                failures.append({"text": example["text"], "expected": gold, "got": listed(found)})
            first = by_code[candidates[0]["code"]]["category"] if candidates else None
            category += first == by_code[example["code"]]["category"]
        total = len(part)
        metrics[f"{label}: шаблон в топ-1"] = (top1, total)
        metrics[f"{label}: шаблон в топ-5"] = (top5, total)
        metrics[f"{label}: MRR"] = reciprocal / total if total else 0.0
        metrics[f"{label}: категория"] = (category, total)
        metrics[f"{label}: кандидатов в среднем"] = sizes / total if total else 0.0
    metrics.update(by_tag(generated, [right[id(e)] for e in generated], "шаблон в топ-1"))
    return {"key": "stage1", "title": "1. Позиция КТРУ", "metrics": metrics, "failures": failures}


def template_names(candidates: list[Candidate], by_code: dict[str, Position]) -> list[str]:
    found: list[str] = []
    for template in templates(candidates, by_code):
        name = named(template, by_code)
        if name not in found:
            found.append(name)
    return found


def by_tag(generated: list[Generated], right: list[bool], what: str) -> dict[str, Number]:
    """Каждая порча и форма записи отдельно: видно, на чём именно этап ошибается."""
    return by_slice([example["tags"] for example in generated], right, what)


def by_slice(labels: list[list[str]], right: list[bool], what: str) -> dict[str, Number]:
    counts: dict[str, list[int]] = {}
    for tags, ok in zip(labels, right, strict=True):
        for tag in tags:
            pair = counts.setdefault(tag, [0, 0])
            pair[0] += ok
            pair[1] += 1
    metrics: dict[str, Number] = {}
    for tag in sorted(counts):
        metrics[f"{what}, {tag}"] = (counts[tag][0], counts[tag][1])
    return metrics


def templates(candidates: list[Candidate], by_code: dict[str, Position]) -> list[str]:
    found: list[str] = []
    for candidate in candidates:
        template = template_of(candidate["code"], by_code)
        if template not in found:
            found.append(template)
    return found


def template_of(code: str, by_code: dict[str, Position]) -> str:
    return by_code[code]["parent_code"] or code


def named(code: str, by_code: dict[str, Position]) -> str:
    position = by_code.get(code)
    return position["name"] if position else code


def listed(items: list[str], shown: int = 3) -> str:
    if not items:
        return "ничего"
    return "; ".join(items[:shown])


def split(generated: list[Generated]) -> list[tuple[str, list[Generated]]]:
    """Шум прячет регресс на чистых текстах, поэтому считаем их отдельно."""
    clean = [e for e in generated if not e["noisy"]]
    spoiled = [e for e in generated if e["noisy"]]
    return [("чистые", clean), ("с порчей", spoiled)]


def stage2(texts: list[tuple[str, str]], portal: Portal) -> Stage:
    """Отказ считаем отдельно от ошибки: «не знаю» лучше уверенно неверного раздела."""
    right = 0
    refused = 0
    failures: list[Failure] = []
    for text, section in texts:
        found = find_section(text, portal)
        right += found == section
        refused += found is None
        if found != section:
            failures.append({"text": text, "expected": section, "got": found or "отказ"})
    return {
        "key": "stage2",
        "title": "2. Раздел портала",
        "metrics": {
            "угадан": (right, len(texts)),
            "отказ": (refused, len(texts)),
            "верно среди ответивших": (right, len(texts) - refused),
        },
        "failures": failures,
    }


def stage2_products(items: list[Product], portal: Portal) -> Stage:
    """Только название товара: метка взята из названий его характеристик, с ними в тексте
    ответ был бы подсказан."""
    labeled = [p for p in items if p["section"]]
    right = 0
    refused = 0
    rights = []
    failures: list[Failure] = []
    for item in labeled:
        found = find_section(item["name"], portal)
        rights.append(found == item["section"])
        right += found == item["section"]
        refused += found is None
        if found != item["section"]:
            failures.append(
                {"text": item["name"], "expected": item["section"] or "", "got": found or "отказ"}
            )
    metrics: dict[str, Number] = {
        "с меткой раздела": (len(labeled), len(items)),
        "угадан": (right, len(labeled)),
        "отказ": (refused, len(labeled)),
        "верно среди ответивших": (right, len(labeled) - refused),
    }
    metrics.update(by_slice([slices(p) for p in labeled], rights, "угадан"))
    return {
        "key": "stage2_sheet1",
        "title": "2. Раздел портала на листе 1",
        "metrics": metrics,
        "failures": failures,
    }


def slices(item: Product) -> list[str]:
    return [CATEGORY_NAMES[item["category"]], *item["tags"]]


def ktru_names(ref: list[Position]) -> dict[str, set[str]]:
    """Названия характеристик КТРУ по категориям: пара листа 1 под таким названием
    должна уйти в КТРУ, остальные в портал или новые."""
    names: dict[str, set[str]] = {}
    for position in ref:
        for char in position["characteristics"]:
            names.setdefault(position["category"], set()).add(char["name"].casefold())
    return names


def pair_slices(item: Product, name: str, known: dict[str, set[str]]) -> list[str]:
    in_ktru = name.casefold() in known.get(item["category"], set())
    return [*slices(item), "название из КТРУ" if in_ktru else "название не из КТРУ"]


def glued(item: Product) -> tuple[str, list[tuple[int, int]]]:
    """Текст товара и место каждой пары в нём."""
    text = item["name"]
    spans = []
    for name, value in item["pairs"]:
        text += ", "
        spans.append((len(text), len(text) + len(name) + 1 + len(value)))
        text += f"{name} {value}"
    return text, spans


def stage3(items: list[Product], ref: list[Position]) -> Stage:
    """Пара найдена, если есть кусок с тем же названием и значением; числа сравниваем числом.
    Мягко: значение нашлось, а название нет, этап 4 ещё может его привязать."""
    known = ktru_names(ref)
    expected = 0
    hits = 0
    soft = 0
    named_count = 0
    labels = []
    rights = []
    failures: list[Failure] = []
    for item in items:
        text = glued(item)[0]
        fragments = extract(text)
        with_name = [f for f in fragments if f["name"]]
        named_count += len(with_name)
        missed = []
        for pair_name, value in item["pairs"]:
            expected += 1
            hit = any(same_pair(f, pair_name, value) for f in with_name)
            labels.append(pair_slices(item, pair_name, known))
            rights.append(hit)
            if hit:
                hits += 1
                continue
            missed.append(f"{pair_name}: {value}")
            if any(same_value(f, value) for f in fragments):
                soft += 1
        if missed:
            got = [f"{f['name']}: {f['value']}" for f in with_name]
            failures.append({"text": text, "expected": "; ".join(missed), "got": listed(got, 10)})
    metrics: dict[str, Number] = {
        "полнота": (hits, expected),
        "точность": (hits, named_count),
        "F1": f_score(hits, named_count, expected, 1),
        "ещё только значение без названия": (soft, expected),
    }
    metrics.update(by_slice(labels, rights, "полнота"))
    return {"key": "stage3", "title": "3. Извлечение", "metrics": metrics, "failures": failures}


def layout(items: list[Product], ref: list[Position], portal: Portal) -> Stage:
    """Живой товар целиком через все этапы: в какой уровень ушла каждая пара листа 1.
    Точного ответа нет, но пара под названием из КТРУ должна попасть в КТРУ, пара из
    раздела-метки хотя бы в портал, и ни одна пара не должна потеряться."""
    known = ktru_names(ref)
    levels = dict.fromkeys([*LEVELS.values(), "потеряно"], 0)
    renamed = 0
    placed = 0
    ktru_wanted = 0
    ktru_got = 0
    section_wanted = 0
    section_got = 0
    labels = []
    rights = []
    failures: list[Failure] = []
    for item in laid_out(items):
        text, spans = glued(item)
        result = solve(text, ref, portal)
        bad = []
        for (name, value), span in zip(item["pairs"], spans, strict=True):
            char = covering(result["chars"], span)
            level = LEVELS[char["source"]] if char else "потеряно"
            levels[level] += 1
            kept = level in ("КТРУ", "портал")
            if char:
                placed += 1
                renamed += char["name"].casefold() != name.casefold()
            pair_labels = pair_slices(item, name, known)
            if "название из КТРУ" in pair_labels:
                ktru_wanted += 1
                ktru_got += level == "КТРУ"
            if item["section"]:
                section_wanted += 1
                section_got += kept
            labels.append(pair_labels)
            rights.append(kept)
            if not kept:
                bad.append(f"{name}: {value} ({level})")
        if bad:
            got = f"раздел {result['section'] or 'не найден'}, ждали {item['section'] or '?'}"
            failures.append({"text": text, "expected": "; ".join(bad), "got": got})
    total = sum(levels.values())
    metrics: dict[str, Number] = {}
    for level, count in levels.items():
        metrics[f"пары: {level}"] = (count, total)
    metrics["название приведено к другому"] = (renamed, placed)
    metrics["название из КТРУ ушло в КТРУ"] = (ktru_got, ktru_wanted)
    metrics["есть метка раздела: пара в КТРУ или портале"] = (section_got, section_wanted)
    metrics.update(by_slice(labels, rights, "в КТРУ или портале"))
    return {
        "key": "layout_sheet1",
        "title": "Лист 1 целиком: в какой уровень ушла пара",
        "metrics": metrics,
        "failures": failures,
    }


def laid_out(items: list[Product]) -> list[Product]:
    """Полный прогон медленный, поэтому поровну из каждой категории."""
    found = []
    for category in CATEGORY_NAMES:
        found += [p for p in items if p["category"] == category][:LAID_OUT]
    return found


def covering(chars: list[FoundChar], span: tuple[int, int]) -> FoundChar | None:
    for char in chars:
        if span[0] <= char["start"] < span[1]:
            return char
    return None


def same_pair(item: Fragment, name: str, value: str) -> bool:
    if (item["name"] or "").casefold() != name.casefold():
        return False
    return same_value(item, value)


def same_value(item: Fragment, value: str) -> bool:
    number = NUMBER.search(value)
    if item["number"] is not None and number:
        return item["number"] == float(number["number"].replace(",", "."))
    return item["value"].casefold() == value.casefold()


def f_score(right: int, found: int, expected: int, beta: float) -> float:
    """beta 0.5 для правок: ложная правка чистого значения хуже пропущенной."""
    if not right:
        return 0.0
    precision = right / found
    recall = right / expected
    return (1 + beta**2) * precision * recall / (beta**2 * precision + recall)


def stage4(generated: list[Generated], by_code: dict[str, Position], ref: list[Position]) -> Stage:
    expected = 0
    right = 0
    right_unit = 0
    extra = 0
    corrupted = 0
    fixes = 0
    right_fixes = 0
    false_alarms = 0
    clean = 0
    by_kind: dict[str, list[int]] = {}
    failures: list[Failure] = []
    right_examples = []
    for example in generated:
        position = by_code[example["code"]]
        candidates = siblings(position, by_code)
        found = link(example["fragments"], candidates, None, ref, {})
        names = {char["name"] for char in example["chars"]}
        extras = sum(char["name"] not in names for char in found)
        extra += extras
        bad = extras > 0
        for char in example["chars"]:
            expected += 1
            got = [
                f for f in found if f["name"] == char["name"] and same(f["value"], char["value"])
            ]
            right += bool(got)
            right_unit += any(unit_ok(f, position) for f in got)
            fixed = [f for f in found if f["name"] == char["name"] and f["kind"]]
            fixes += bool(fixed)
            bad = bad or not got
            if "kind" not in char:
                clean += 1
                false_alarms += bool(fixed)
                bad = bad or bool(fixed)
                continue
            corrupted += 1
            hit = any(f["kind"] == char["kind"] for f in got)
            right_fixes += hit
            bad = bad or not hit
            counts = by_kind.setdefault(char["kind"], [0, 0])
            counts[0] += hit
            counts[1] += 1
        right_examples.append(not bad)
        if bad:
            wanted = [described(c["name"], c["value"], c.get("kind")) for c in example["chars"]]
            got_all = [described(f["name"], f["value"], f["kind"], f["rule"]) for f in found]
            failures.append(
                {"text": example["text"], "expected": "; ".join(wanted), "got": listed(got_all, 99)}
            )
    metrics: dict[str, Number] = {
        "название и значение": (right, expected),
        "с единицей": (right_unit, expected),
        "лишних": extra,
        "правки: исправлено": (right_fixes, corrupted),
        "правки: верных среди правок": (right_fixes, fixes),
        "правки: F0.5": f_score(right_fixes, fixes, corrupted, 0.5),
        "правки: ложных на чистых": (false_alarms, clean),
    }
    for kind, (hit, total) in by_kind.items():
        metrics[f"порча {kind}: исправлено"] = (hit, total)
    metrics["пример верен целиком"] = (sum(right_examples), len(generated))
    metrics.update(by_tag(generated, right_examples, "пример верен"))
    return {
        "key": "stage4",
        "title": "4. Привязка и унификация",
        "metrics": metrics,
        "failures": failures,
    }


def stage34(
    generated: list[Generated], by_code: dict[str, Position], ref: list[Position], portal: Portal
) -> Stage:
    """Извлечение и привязка вместе на синтетике: числа словами, габариты и диапазоны
    видны только так, на правильных кусках их уже разобрал генератор. Правки не судим."""
    right = []
    failures: list[Failure] = []
    for example in generated:
        candidates = siblings(by_code[example["code"]], by_code)
        result = solve(example["text"], ref, portal, find=given(candidates))
        found = [char for char in result["chars"] if char["source"] == "ktru"]
        names = {char["name"] for char in example["chars"]}
        missed = []
        for char in example["chars"]:
            if not any(
                f["name"] == char["name"] and same(f["value"], char["value"]) for f in found
            ):
                missed.append(described(char["name"], char["value"]))
        extra = [described(f["name"], f["value"]) for f in found if f["name"] not in names]
        right.append(not missed and not extra)
        if missed or extra:
            got = [described(f["name"], f["value"], f["rule"]) for f in found]
            wanted = "; ".join(missed) or "ничего лишнего"
            failures.append({"text": example["text"], "expected": wanted, "got": listed(got, 99)})
    metrics: dict[str, Number] = {"пример верен целиком": (sum(right), len(generated))}
    metrics.update(by_tag(generated, right, "пример верен"))
    return {
        "key": "stage34",
        "title": "3–4. Извлечение и привязка вместе",
        "metrics": metrics,
        "failures": failures,
    }


def described(name: str, value: str | int | float, *notes: str | None) -> str:
    """Имя правила в выводе: в провале сразу видно, что сработало не так."""
    text = f"{name}: {show(value)}"
    kept = [note for note in notes if note]
    if kept:
        text += f" ({', '.join(kept)})"
    return text


def unit_ok(found: FoundChar, position: Position) -> bool:
    """Единица КТРУ у числа, у текстового значения единицы нет."""
    for char in position["characteristics"]:
        if char["name"] == found["name"] and "unit" in char:
            return found["unit"] is not None and found["unit"]["code"] == char["unit"]["code"]
    return found["unit"] is None


def given(candidates: list[Candidate]) -> FindCandidates:
    def find_given(text: str, ref: list[Position]) -> list[Candidate]:
        return candidates

    return find_given


def stage5(generated: list[Generated], by_code: dict[str, Position], ref: list[Position]) -> Stage:
    """Со всеми неизменяемыми значениями код должен найтись. Без одного допустим вопрос,
    но не уверенный неверный код; после ответа на вопрос код должен найтись."""
    rng = Random(0)
    full_right = 0
    right = 0
    asked = 0
    silent = 0
    answered_right = 0
    required = 0
    required_shown = 0
    failures: list[Failure] = []
    for example in generated:
        position = by_code[example["code"]]
        candidates = siblings(position, by_code)
        chars = fixed_chars(position)
        problems = []
        full = narrow(candidates, chars, ref)
        full_right += full["code"] == example["code"]
        if full["code"] != example["code"]:
            problems.append(f"со всеми значениями {outcome(full)}")
        if chars:
            removed = chars.pop(rng.randrange(len(chars)))
            if is_required(removed["name"], position):
                required += 1
                missing = narrow(candidates, chars, ref)["missing_required"]
                required_shown += removed["name"] in missing
        result = narrow(candidates, chars, ref)
        right += result["code"] == example["code"]
        if result["code"] is not None and result["code"] != example["code"]:
            silent += 1
            problems.append(f"без одного значения молча {result['code']}")
        if result["question"]:
            asked += 1
            answer = answer_char(result["question"]["char"], position)
            if answer:
                chars.append(answer)
            after = narrow(candidates, chars, ref)
            answered_right += after["code"] == example["code"]
            if after["code"] != example["code"]:
                problems.append(f"после ответа {outcome(after)}")
        if problems:
            failures.append(
                {"text": example["text"], "expected": example["code"], "got": "; ".join(problems)}
            )
    total = len(generated)
    return {
        "key": "stage5",
        "title": "5. Сужение",
        "metrics": {
            "со всеми значениями код верен": (full_right, total),
            "без одного: код верен": (right, total),
            "без одного: вопрос": (asked, total),
            "без одного: молча неверно": silent,
            "после ответа код верен": (answered_right, asked),
            "убранная обязательная показана в недостающих": (required_shown, required),
        },
        "failures": failures,
    }


def outcome(result: Narrowed) -> str:
    if result["code"]:
        return f"код {result['code']}"
    if result["question"]:
        return f"вопрос про {result['question']['char']}"
    return "нет кода"


def is_required(name: str, position: Position) -> bool:
    return any(c["name"] == name and c["required"] for c in position["characteristics"])


def answer_char(name: str, position: Position) -> FoundChar | None:
    for char in fixed_chars(position):
        if char["name"] == name:
            return char
    return None


def fixed_chars(position: Position) -> list[FoundChar]:
    found = []
    for char in position["characteristics"]:
        if char["selection"] != "fixed" or not char["values"]:
            continue
        first = char["values"][0]
        value = first if isinstance(first, str) else inside(first)
        if value is not None:
            found.append(gold_char({"name": char["name"], "value": value}))
    return found


def stages(ref: list[Position], portal: Portal, with_products: bool = True) -> list[Stage]:
    by_code = {p["code"]: p for p in ref}
    generated = generate(ref, GENERATED)
    found = [stage1(generated, ref), stage2(section_texts(portal, SECTIONS), portal)]
    items = products(portal, build=with_products)
    if items:
        found += [stage2_products(items, portal), stage3(items, ref), layout(items, ref, portal)]
    found += [
        stage4(generated, by_code, ref),
        stage34(generated, by_code, ref, portal),
        stage5(generated, by_code, ref),
    ]
    return found


def compare(stage: Stage, last_dir: Path = LAST_DIR) -> str:
    """Сравнение с прошлым прогоном и сохранение нового: после правки правила видно, какие
    примеры сломались, а не только сдвиг процента."""
    path = last_dir / f"{stage['key']}.json"
    previous = load_stage(path)
    lines = [stage["title"]]
    for name, value in stage["metrics"].items():
        line = f"  {name}: {shown(value)}"
        if previous and name in previous["metrics"]:
            old = shown(previous["metrics"][name])
            if old != shown(value):
                line += f" (было {old})"
        lines.append(line)
    if previous:
        before = {failure_key(f) for f in previous["failures"]}
        now = {failure_key(f) for f in stage["failures"]}
        new = [f for f in stage["failures"] if failure_key(f) not in before]
        lines.append(f"  с прошлого прогона: ушло провалов {len(before - now)}, новых {len(new)}")
        for failure in new[:SHOWN_NEW]:
            lines.append(
                f"    «{failure['text']}»: ждали {failure['expected']}, получили {failure['got']}"
            )
    lines.append(f"  провалов {len(stage['failures'])}, все в {path}")
    save_stage(path, stage)
    return "\n".join(lines)


def shown(value: Number | list[int]) -> str:
    if isinstance(value, tuple | list):
        return share(value[0], value[1])
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def failure_key(failure: Failure) -> tuple[str, str, str]:
    return failure["text"], failure["expected"], failure["got"]


def load_stage(path: Path) -> Stage | None:
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def save_stage(path: Path, stage: Stage) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as target:
        json.dump(stage, target, ensure_ascii=False, indent=1)


def report(ref: list[Position], portal: Portal, with_products: bool = True) -> str:
    found = stages(ref, portal, with_products)
    return "Этапы на правильном входе\n" + "\n".join(compare(stage) for stage in found)


if __name__ == "__main__":
    from pipeline.data import portal, reference

    print(report(reference(), portal(), with_products="--fast" not in sys.argv))
