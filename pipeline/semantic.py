import re
import sqlite3
from collections.abc import Callable
from difflib import SequenceMatcher, get_close_matches
from functools import cache
from pathlib import Path
from typing import Literal, cast

from contracts.pipeline import (
    Candidate,
    Candidates,
    CharStatus,
    FoundChar,
    Interpretation,
    Knowledge,
    LearningDecision,
    PipelineSteps,
    PortalCandidate,
    Question,
    RejectedFragment,
    Result,
    TextEdit,
)
from contracts.ref import (
    LearnedCharacteristic,
    LearnedContextKind,
    LearnedValueType,
    Portal,
    PortalChar,
    Position,
    Unit,
)
from pipeline.candidates import find as lexical_find
from pipeline.chars import UNIT_ALIASES, convert, extract, scale, tidy
from pipeline.memory import DEFAULT_MEMORY_PATH, load_all, normalize_key, save_characteristic
from pipeline.narrow import narrow
from pipeline.text import correct, stems, unknown, vocabulary, without_pairs, words
from pipeline.values import show
from scripts.dataloader import load_data, load_portal

MAX_KTRU_CANDIDATES = 20
MAX_PORTAL_CANDIDATES = 3
KTRU_THRESHOLD = 0.45
PORTAL_THRESHOLD = 0.52

CONCEPTS = {
    "верстак": "стол",
    "офисный": "письменный",
    "рабочий": "письменный",
    "ростовка": "рост",
    "габарит": "размер",
    "масса": "вес",
    "окраска": "цвет",
    "оттенок": "цвет",
    "сырье": "материал",
    "сырья": "материал",
}
GARBAGE_NAMES = {
    "акция",
    "артикул",
    "бренд",
    "вкус",
    "доставка",
    "лучший",
    "продавец",
    "скидка",
    "ссылка",
    "стоимость",
    "цена",
}
CHAR_HINTS = {
    "вес",
    "высо",
    "глуби",
    "диагон",
    "диамет",
    "дли",
    "защел",
    "класс",
    "количе",
    "кром",
    "матери",
    "мощнос",
    "налич",
    "объем",
    "плотно",
    "покры",
    "размер",
    "скорос",
    "состав",
    "тип",
    "толщи",
    "фор",
    "цве",
    "шири",
}
CATEGORY_MARKERS = {
    "clothing": stems("одежда обувь рукав воротник манжета ткань размер ноги"),
    "furniture": stems("столешница каркас ножка сиденье спинка полка фасад тумба"),
    "tools": stems("патрон двигатель лезвие рукоятка сверло удар аккумулятор"),
}
UNIT_FORMS = {
    **UNIT_ALIASES,
    "миллиметр": "003",
    "миллиметра": "003",
    "миллиметров": "003",
    "сантиметр": "004",
    "сантиметра": "004",
    "сантиметров": "004",
    "метр": "006",
    "метра": "006",
    "метров": "006",
    "дюйма": "039",
    "дюймов": "039",
    "грамм": "163",
    "грамма": "163",
    "граммов": "163",
    "килограмм": "166",
    "килограмма": "166",
    "килограммов": "166",
}
UNIT_NAMES = {
    "003": "Миллиметр",
    "004": "Сантиметр",
    "006": "Метр",
    "008": "Километр",
    "039": "Дюйм",
    "163": "Грамм",
    "166": "Килограмм",
    "796": "Штука",
}
SIMPLE_NUMBER = re.compile(r"^\s*(?P<number>\d+(?:[.,]\d+)?)\s*(?P<unit>[A-Za-zА-Яа-яЁё/%]+)?\s*$")

LoadLearned = Callable[[str | Path], list[dict]]
SaveLearned = Callable[[str | Path, dict], dict]


@cache
def reference() -> list[Position]:
    return load_data()


@cache
def portal_reference() -> Portal:
    return load_portal()


def semantic_retrieve(text: str, knowledge: Knowledge) -> Candidates:
    positions = knowledge["positions"]
    baseline = lexical_find(text, positions)
    product = without_pairs(text)
    candidates = baseline["candidates"]
    if not candidates:
        candidates = rank_ktru(product, positions)

    query = product
    if candidates:
        query = f"{query} {' '.join(candidate['name'] for candidate in candidates)}"
    portal_categories = rank_portal(query, knowledge["portal"])
    category = baseline["category"]
    if category is None and candidates:
        by_code = {position["code"]: position for position in positions}
        category = by_code[candidates[0]["code"]]["category"]
    return {
        "category": category,
        "candidates": candidates,
        "portal_categories": portal_categories,
    }


def rank_ktru(text: str, positions: list[Position]) -> list[Candidate]:
    ranked = []
    for position in positions:
        if position["position_type"] != "final":
            continue
        name_score = similarity(text, position["name"])
        evidence = characteristic_evidence(text, position)
        score = 0.85 * name_score + 0.15 * evidence
        if score >= KTRU_THRESHOLD:
            ranked.append((score, position))
    ranked.sort(key=lambda item: (-item[0], item[1]["code"]))
    if not ranked:
        return []
    best = ranked[0][0]
    kept = [item for item in ranked if item[0] >= best - 0.08][:MAX_KTRU_CANDIDATES]
    total = sum(score for score, _ in kept) or 1
    return [
        {"code": position["code"], "name": position["name"], "score": score / total}
        for score, position in kept
    ]


def rank_portal(text: str, portal: Portal) -> list[PortalCandidate]:
    ranked = [(similarity(text, category), category) for category in portal]
    ranked = [item for item in ranked if item[0] >= PORTAL_THRESHOLD]
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [
        {"name": category, "score": round(score, 4)}
        for score, category in ranked[:MAX_PORTAL_CANDIDATES]
    ]


def semantic_interpret(text: str, candidates: Candidates, knowledge: Knowledge) -> Interpretation:
    portal_category = selected_portal(candidates)
    raw = extract(text, candidates, knowledge["positions"], knowledge["portal"])
    accepted: list[FoundChar] = []
    unmatched: list[FoundChar] = []
    for char in raw:
        if char["source"] == "ktru":
            char["status"] = status_for(char)
            accepted.append(char)
        else:
            unmatched.append(char)

    portal_chars = knowledge["portal"].get(portal_category or "", [])
    learned = scoped_learned(
        candidates, portal_category, text, knowledge["learned"], knowledge["positions"]
    )
    rejected: list[RejectedFragment] = []
    for char in unmatched:
        mapped = map_portal(char, portal_chars)
        if mapped is None:
            mapped = map_learned(char, learned)
        if mapped is not None:
            accepted.append(mapped)
            continue
        reason = rejection_reason(char, candidates, portal_category)
        if reason:
            rejected.append(rejected_char(char, reason))
            continue
        char["status"] = "accepted"
        normalize_new_value(char)
        accepted.append(char)

    occupied = [(char["start"], char["end"]) for char in accepted + unmatched]
    product_stems = stems(" ".join(item["name"] for item in candidates["candidates"]))
    accepted.extend(implicit_portal(text, portal_chars, occupied, product_stems))
    accepted = prefer_authoritative(accepted)
    return {
        "chars": accepted,
        "rejected": rejected,
        "portal_category": portal_category,
    }


def semantic_decide(
    candidates: Candidates, interpretation: Interpretation, knowledge: Knowledge
) -> Result:
    result = narrow(candidates, interpretation["chars"], knowledge["positions"])
    if result["code"] is None:
        result["missing_required"] = []

    questions: list[Question] = []
    if result["question"]:
        questions.append({**result["question"], "kind": "disambiguation"})
    if result["code"]:
        for name in result["missing_required"]:
            questions.append(
                {
                    "char": name,
                    "options": result["options"].get(name, []),
                    "kind": "required",
                    "reason": "обязательная характеристика выбранного кода КТРУ",
                }
            )
    for char in interpretation["chars"]:
        if char.get("status") != "needs_confirmation":
            continue
        questions.append(
            {
                "char": char["name"],
                "options": result["options"].get(char["name"], []),
                "kind": "confirmation",
                "reason": char["fix"] or "значение нужно уточнить",
            }
        )

    result["question"] = questions[0] if questions else None
    result["questions"] = questions
    result["rejected"] = interpretation["rejected"]
    result["portal_category"] = interpretation["portal_category"]
    result["status"] = outcome_status(result, interpretation)
    return result


DEFAULT_STEPS: PipelineSteps = {
    "retrieve": semantic_retrieve,
    "interpret": semantic_interpret,
    "decide": semantic_decide,
}


def run(
    text: str,
    learn: bool = False,
    *,
    memory_path: str | Path = DEFAULT_MEMORY_PATH,
    positions: list[Position] | None = None,
    portal: Portal | None = None,
    steps: PipelineSteps = DEFAULT_STEPS,
    load_learned: LoadLearned = load_all,
    save_learned: SaveLearned = save_characteristic,
) -> Result:
    memory_unavailable = False
    try:
        learned = loaded_learned(load_learned(memory_path))
    except OSError, sqlite3.Error, ValueError, TypeError:
        learned = []
        memory_unavailable = True
    knowledge: Knowledge = {
        "positions": positions if positions is not None else reference(),
        "portal": portal if portal is not None else portal_reference(),
        "learned": learned,
    }
    candidates = steps["retrieve"](text, knowledge)
    interpretation = steps["interpret"](text, candidates, knowledge)
    result = steps["decide"](candidates, interpretation, knowledge)

    result["learning"] = learn_new(
        interpretation["chars"],
        candidates,
        interpretation["portal_category"],
        text,
        knowledge["positions"],
        memory_path,
        learn,
        save_learned,
    )
    if memory_unavailable:
        result["learning"].append(
            {
                "name": "Локальная память",
                "action": "failed",
                "context": "",
                "reason": "справочники обработаны без локально выученных характеристик",
            }
        )
    result["edits"] = text_edits(text, candidates, interpretation["chars"])
    result["corrected_text"] = apply_edits(text, result["edits"])
    result["remarks"] = [
        {"was": edit["was"], "now": edit["now"], "why": edit["why"], "kind": edit["kind"]}
        for edit in result["edits"]
    ]
    covered = [(char["start"], char["end"]) for char in interpretation["chars"]]
    covered += [(item["start"], item["end"]) for item in interpretation["rejected"]]
    result["unknown"] = unknown(text, covered, known_stems(candidates, interpretation, text))
    result["implementation"] = "semantic-hybrid-v1"
    return result


def similarity(left: str, right: str) -> float:
    left_terms = semantic_terms(left)
    right_terms = semantic_terms(right)
    if not left_terms or not right_terms:
        return 0.0
    hits = len(left_terms & right_terms)
    coverage = hits / len(right_terms)
    containment = hits / min(len(left_terms), len(right_terms))
    sequence = SequenceMatcher(None, normalize_key(left), normalize_key(right)).ratio()
    return max(coverage, containment * 0.9, sequence * 0.65)


def semantic_terms(text: str) -> set[str]:
    raw = normalize_key(text).split()
    expanded = [CONCEPTS.get(word, word) for word in raw]
    return stems(" ".join(expanded))


def characteristic_evidence(text: str, position: Position) -> float:
    haystack = semantic_terms(text)
    if not haystack:
        return 0.0
    concepts = []
    for char in position["characteristics"]:
        concepts.append(char["name"])
        concepts.extend(str(value) for value in char["values"] if isinstance(value, str))
    wanted = semantic_terms(" ".join(concepts))
    return min(1.0, len(haystack & wanted) / 3)


def selected_portal(candidates: Candidates) -> str | None:
    portal = candidates.get("portal_categories", [])
    if not portal:
        return None
    first = portal[0]
    if first["score"] < PORTAL_THRESHOLD:
        return None
    if len(portal) > 1 and first["score"] - portal[1]["score"] < 0.03:
        return None
    return first["name"]


def status_for(char: FoundChar) -> CharStatus:
    if char["kind"] in {"out_of_range", "not_in_list", "conflict"}:
        return "needs_confirmation"
    if char["kind"] is not None:
        return "corrected"
    return "accepted"


def map_portal(char: FoundChar, definitions: list[PortalChar]) -> FoundChar | None:
    definition = named_portal(char["name"], definitions)
    if definition is None:
        return None
    mapped = char.copy()
    mapped["name"] = canonical_name(definition["name"])
    mapped["source"] = "portal"
    mapped["required"] = False
    mapped["status"] = "accepted"
    normalize_catalog_value(mapped, definition["values"], definition["unit"], "Портале")
    return mapped


def named_portal(name: str, definitions: list[PortalChar]) -> PortalChar | None:
    wanted = normalize_key(name)
    exact = {normalize_key(item["name"]): item for item in definitions}
    if wanted in exact:
        return exact[wanted]
    close = get_close_matches(wanted, exact, n=1, cutoff=0.84)
    if close:
        return exact[close[0]]
    wanted_stems = stems(name)
    matches = [item for item in definitions if wanted_stems and wanted_stems <= stems(item["name"])]
    return matches[0] if len(matches) == 1 else None


def map_learned(char: FoundChar, learned: list[LearnedCharacteristic]) -> FoundChar | None:
    wanted = normalize_key(char["name"])
    matched = None
    for definition in learned:
        names = [definition["name"], *definition["aliases"]]
        keys = {normalize_key(name) for name in names}
        if wanted in keys or get_close_matches(wanted, keys, n=1, cutoff=0.86):
            if matched is not None:
                return None
            matched = definition
    if matched is None:
        return None
    mapped = char.copy()
    mapped["name"] = matched["name"]
    mapped["source"] = "learned"
    mapped["required"] = False
    mapped["status"] = "accepted"
    if wanted != normalize_key(matched["name"]):
        mapped["kind"] = "typo"
        mapped["fix"] = f"ранее характеристика сохранена как «{matched['name']}»"
        mapped["status"] = "corrected"
    normalize_open_value(mapped, matched["unit"])
    return mapped


def normalize_catalog_value(
    char: FoundChar, allowed: list[str], unit_name: str | None, source_name: str
) -> None:
    raw = str(char["value"]).strip()
    options = {normalize_key(value): value for value in allowed}
    if options:
        key = normalize_key(raw)
        if key in options:
            char["value"] = options[key]
        else:
            close = get_close_matches(key, options, n=1, cutoff=0.78)
            if close:
                char["value"] = options[close[0]]
                char["kind"] = "typo"
                char["fix"] = f"опечатка, в {source_name} пишется «{char['value']}»"
                char["status"] = "corrected"
            else:
                char["kind"] = "not_in_list"
                char["fix"] = f"значения нет в {source_name}"
                char["status"] = "needs_confirmation"
    if unit_name:
        normalize_open_value(char, portal_unit(unit_name))


def normalize_open_value(char: FoundChar, target_unit: Unit | None) -> None:
    match = SIMPLE_NUMBER.match(str(char["value"]))
    if match is None:
        if target_unit:
            char["unit"] = target_unit
        return
    value = float(match["number"].replace(",", "."))
    raw_unit = match["unit"]
    source_code = UNIT_FORMS.get((raw_unit or "").casefold())
    if target_unit and source_code:
        if scale(source_code)[0] != scale(target_unit["code"])[0]:
            char["unit"] = target_unit
            char["kind"] = "conflict"
            char["fix"] = "единица несовместима с размерностью сохранённой характеристики"
            char["status"] = "needs_confirmation"
            char["value"] = tidy(value)
            return
        value = convert(value, source_code, target_unit["code"])
        if source_code != target_unit["code"]:
            char["kind"] = "unit"
            char["fix"] = f"перевели в единицу источника: {target_unit['name'].lower()}"
            char["status"] = "corrected"
        char["unit"] = target_unit
    elif raw_unit:
        code = source_code or f"custom:{normalize_key(raw_unit)}"
        char["unit"] = {"code": code, "name": UNIT_NAMES.get(code, raw_unit)}
    elif target_unit:
        char["unit"] = target_unit
    char["value"] = tidy(round(value, 9))


def portal_unit(name: str) -> Unit:
    code = UNIT_FORMS.get(name.casefold(), f"portal:{normalize_key(name)}")
    return {"code": code, "name": UNIT_NAMES.get(code, name)}


def normalize_new_value(char: FoundChar) -> None:
    normalize_open_value(char, None)


def implicit_portal(
    text: str,
    definitions: list[PortalChar],
    occupied: list[tuple[int, int]],
    product_stems: set[str],
) -> list[FoundChar]:
    matches: list[FoundChar] = []
    for definition in definitions:
        for value in definition["values"]:
            if len(value) < 3 or value.casefold() in {"да", "нет"}:
                continue
            if stems(value) and stems(value) <= product_stems:
                continue
            found = re.search(rf"(?<!\w){re.escape(value)}(?!\w)", text, re.IGNORECASE)
            start = found.start() if found else -1
            if start < 0 or any(left <= start < right for left, right in occupied):
                continue
            matches.append(
                {
                    "name": canonical_name(definition["name"]),
                    "value": value,
                    "unit": portal_unit(definition["unit"]) if definition["unit"] else None,
                    "source": "portal",
                    "fragment": text[start : start + len(value)],
                    "start": start,
                    "end": start + len(value),
                    "required": False,
                    "fix": None,
                    "kind": None,
                    "status": "accepted",
                }
            )
    by_span: dict[tuple[int, int], FoundChar] = {}
    for char in matches:
        key = char["start"], char["end"]
        kept = by_span.get(key)
        if kept is None or len(char["name"]) > len(kept["name"]):
            by_span[key] = char
    return list(by_span.values())


def canonical_name(name: str) -> str:
    return name.strip().rstrip(":;").strip()


def rejection_reason(
    char: FoundChar, candidates: Candidates, portal_category: str | None
) -> str | None:
    name_key = normalize_key(char["name"])
    value_key = normalize_key(str(char["value"]))
    if any(word in name_key.split() for word in GARBAGE_NAMES):
        return "коммерческое, субъективное или не относящееся к товару поле"
    if not value_key or "http" in str(char["value"]).casefold():
        return "значение пустое или похоже на ссылку"
    category = candidates["category"]
    char_stems = stems(char["name"])
    if category:
        foreign = set().union(
            *(markers for name, markers in CATEGORY_MARKERS.items() if name != category)
        )
        if char_stems & foreign and not char_stems & CATEGORY_MARKERS[category]:
            return "характеристика относится к другому виду товара"
    hints = char_stems & CHAR_HINTS
    product = stems(" ".join(item["name"] for item in candidates["candidates"]))
    related = bool(char_stems & product)
    if not hints and not related and len(words(char["name"])) < 2:
        source = "КТРУ и категории Портала" if portal_category else "КТРУ"
        return f"поле не похоже на характеристику товара и отсутствует в {source}"
    return None


def rejected_char(char: FoundChar, reason: str) -> RejectedFragment:
    garbage = any(word in normalize_key(char["name"]) for word in GARBAGE_NAMES)
    return {
        "fragment": char["fragment"],
        "start": char["start"],
        "end": char["end"],
        "reason": reason,
        "kind": "garbage" if garbage else "unmatched",
    }


def prefer_authoritative(chars: list[FoundChar]) -> list[FoundChar]:
    priority = {"ktru": 0, "portal": 1, "learned": 2, "new": 3}
    kept: dict[str, FoundChar] = {}
    for char in sorted(chars, key=lambda item: priority[item["source"]]):
        key = normalize_key(char["name"])
        kept.setdefault(key, char)
    return sorted(kept.values(), key=lambda item: item["start"])


def scoped_learned(
    candidates: Candidates,
    portal_category: str | None,
    text: str,
    learned: list[LearnedCharacteristic],
    positions: list[Position],
) -> list[LearnedCharacteristic]:
    contexts = set(contexts_for(candidates, portal_category, text, positions))
    return [item for item in learned if (item["context_kind"], item["context_key"]) in contexts]


def contexts_for(
    candidates: Candidates,
    portal_category: str | None,
    text: str,
    positions: list[Position],
) -> list[tuple[str, str]]:
    contexts: list[tuple[str, str]] = []
    if candidates["candidates"]:
        by_code = {position["code"]: position for position in positions}
        keys = {
            (by_code[char["code"]].get("parent_code") or char["code"])
            for char in candidates["candidates"]
            if char["code"] in by_code
        }
        if len(keys) == 1:
            contexts.append(("ktru_family", keys.pop()))
    if not contexts and portal_category:
        contexts.append(("portal_category", portal_category))
    if not contexts:
        free = free_product_key(text)
        if free:
            contexts.append(("free_product", free))
    return contexts


def learning_context(
    candidates: Candidates,
    portal_category: str | None,
    text: str,
    positions: list[Position],
) -> tuple[str, str] | None:
    contexts = contexts_for(candidates, portal_category, text, positions)
    return contexts[0] if contexts else None


def free_product_key(text: str) -> str | None:
    product = re.split(r"[,;\n]", without_pairs(text), maxsplit=1)[0]
    terms = sorted(stems(product))
    if not terms:
        return None
    return " ".join(terms[:6])


def learn_new(
    chars: list[FoundChar],
    candidates: Candidates,
    portal_category: str | None,
    text: str,
    positions: list[Position],
    path: str | Path,
    commit: bool,
    save: SaveLearned,
) -> list[LearningDecision]:
    context = learning_context(candidates, portal_category, text, positions)
    decisions: list[LearningDecision] = []
    for char in chars:
        if char["source"] == "learned":
            if commit and context and char.get("status") != "needs_confirmation":
                try:
                    save(path, proposal_for(char, context))
                except OSError, sqlite3.Error, ValueError, TypeError:
                    decisions.append(
                        {
                            "name": char["name"],
                            "action": "failed",
                            "context": context_label(context),
                            "reason": "характеристика распознана, но подтверждение не записано",
                        }
                    )
                    continue
            decisions.append(
                {"name": char["name"], "action": "reused", "context": context_label(context)}
            )
            continue
        if char["source"] != "new":
            continue
        if context is None:
            decisions.append(
                {
                    "name": char["name"],
                    "action": "rejected",
                    "context": "",
                    "reason": "не удалось надёжно определить вид товара",
                }
            )
            continue
        if not commit:
            decisions.append(
                {
                    "name": char["name"],
                    "action": "pending",
                    "context": context_label(context),
                    "reason": "будет сохранена после явного запуска подбора",
                }
            )
            continue
        proposal = proposal_for(char, context)
        try:
            saved = save(path, proposal)
            action = saved.get("action", "created")
            char["learned_now"] = True
            decisions.append(
                {"name": char["name"], "action": action, "context": context_label(context)}
            )
        except OSError, sqlite3.Error, ValueError, TypeError:
            decisions.append(
                {
                    "name": char["name"],
                    "action": "failed",
                    "context": context_label(context),
                    "reason": "результат сформирован, но локальная память недоступна",
                }
            )
    return decisions


def proposal_for(char: FoundChar, context: tuple[str, str]) -> dict:
    normalized_value = normalize_key(str(char["value"]))
    if normalized_value in {"да", "нет", "true", "false"}:
        value_type = "boolean"
    else:
        value_type = "number" if isinstance(char["value"], int | float) else "text"
    unit = char["unit"]
    raw_name = char["fragment"].split(":", 1)[0].strip()
    aliases = []
    if raw_name and normalize_key(raw_name) != normalize_key(char["name"]):
        aliases.append(raw_name)
    return {
        "context_kind": context[0],
        "context_key": context[1],
        "name": char["name"],
        "aliases": aliases,
        "value_type": value_type,
        "unit_code": unit["code"] if unit else None,
        "unit_name": unit["name"] if unit else None,
    }


def context_label(context: tuple[str, str] | None) -> str:
    if context is None:
        return ""
    labels = {
        "ktru_family": "семейство КТРУ",
        "portal_category": "категория Портала",
        "free_product": "вид товара",
    }
    return f"{labels[context[0]]}: {context[1]}"


def loaded_learned(rows: list[dict]) -> list[LearnedCharacteristic]:
    loaded: list[LearnedCharacteristic] = []
    for row in rows:
        unit: Unit | None = cast(Unit | None, row.get("unit"))
        if unit is None and row.get("unit_code") and row.get("unit_name"):
            unit = {
                "code": cast(str, row["unit_code"]),
                "name": cast(str, row["unit_name"]),
            }
        item: LearnedCharacteristic = {
            "id": cast(int, row["id"]),
            "context_kind": cast(LearnedContextKind, row["context_kind"]),
            "context_key": cast(str, row["context_key"]),
            "name": cast(str, row["name"]),
            "aliases": cast(list[str], row.get("aliases", [])),
            "value_type": cast(LearnedValueType, row["value_type"]),
            "unit": unit,
            "confirmations": cast(int, row.get("confirmations", 1)),
        }
        loaded.append(item)
    return loaded


def outcome_status(
    result: Result, interpretation: Interpretation
) -> Literal["ready", "needs_input", "portal_only", "learned_only", "not_found"]:
    if result.get("questions"):
        return "needs_input"
    if result["code"]:
        return "ready"
    if interpretation["portal_category"]:
        return "portal_only"
    if any(char["source"] in {"new", "learned"} for char in interpretation["chars"]):
        return "learned_only"
    return "not_found"


def text_edits(text: str, candidates: Candidates, chars: list[FoundChar]) -> list[TextEdit]:
    edits = char_edits(chars)
    taken = [(edit["start"], edit["end"]) for edit in edits]
    vocab = vocabulary(candidate["name"] for candidate in candidates["candidates"])
    canonical = {stem: word for word, stem in vocab.items()}
    for word in correct(words(without_pairs(text)), vocab):
        if word["fix"] is None or word["stem"] not in canonical:
            continue
        if any(start <= word["start"] < end for start, end in taken):
            continue
        now = canonical[word["stem"]]
        was = text[word["start"] : word["end"]]
        if was.casefold() == now.casefold():
            continue
        why = "исправлена опечатка в названии товара"
        if word["fix"] == "layout":
            why = "исправлена раскладка клавиатуры"
        edits.append(
            {
                "start": word["start"],
                "end": word["end"],
                "was": was,
                "now": now,
                "why": why,
                "kind": word["fix"],
            }
        )
    return non_overlapping(edits)


def char_edits(chars: list[FoundChar]) -> list[TextEdit]:
    edits: list[TextEdit] = []
    for char in chars:
        if char["kind"] not in {"typo", "layout", "unit", "wrong_unit"}:
            continue
        now = show(char["value"])
        if char["unit"]:
            now = f"{now} {char['unit']['name'].lower()}"
        if ":" in char["fragment"]:
            now = f"{char['name']}: {now}"
        edits.append(
            {
                "start": char["start"],
                "end": char["end"],
                "was": char["fragment"],
                "now": now,
                "why": char["fix"] or "приведено к формату источника",
                "kind": char["kind"],
            }
        )
    return edits


def non_overlapping(edits: list[TextEdit]) -> list[TextEdit]:
    kept = []
    for edit in sorted(edits, key=lambda item: (item["start"], -(item["end"] - item["start"]))):
        if any(not (edit["end"] <= item["start"] or edit["start"] >= item["end"]) for item in kept):
            continue
        kept.append(edit)
    return sorted(kept, key=lambda item: item["start"])


def apply_edits(text: str, edits: list[TextEdit]) -> str:
    corrected = text
    for edit in reversed(edits):
        corrected = corrected[: edit["start"]] + edit["now"] + corrected[edit["end"] :]
    return corrected


def known_stems(candidates: Candidates, interpretation: Interpretation, text: str) -> set[str]:
    names = [candidate["name"] for candidate in candidates["candidates"]]
    if interpretation["portal_category"]:
        names.append(interpretation["portal_category"])
    names.extend(char["name"] for char in interpretation["chars"])
    names.append(re.split(r"[,;\n]", text, maxsplit=1)[0])
    return stems(" ".join(names))
