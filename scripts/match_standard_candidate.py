"""Match STE rows (xlsx) to KTRU records (JSON) via NLP-based blocking.

Parses both sides (stages A and B), builds a token inverted index over KTRU
names and finds candidate (STE, KTRU) pairs whose name similarity passes a
threshold (stage C). Output is the original STE table with the best KTRU code
written into a "Код КТРУ" column, plus a flat sheet of all candidate pairs.
"""

import argparse
import gzip
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
WS_RE = re.compile(r"\s+")
TAIL_RE = re.compile(r"(-?\d+(?:[.,]\d+)?)\s*([A-Za-zА-Яа-яЁё/%²³]*)\s*$")
STOPWORDS = {
    "для",
    "или",
    "при",
    "под",
    "над",
    "без",
    "как",
    "что",
    "это",
    "тип",
    "вид",
    "все",
    "всех",
    "них",
    "нее",
    "неё",
    "его",
    "еще",
}


def norm(s: str) -> str:
    """Lowercase, replace ё, strip punctuation, collapse whitespace."""
    if not s:
        return ""
    s = str(s).lower().replace("ё", "е")
    s = PUNCT_RE.sub(" ", s)
    return WS_RE.sub(" ", s).strip()


def tokens(s: str) -> set[str]:
    """Return meaningful tokens (length > 2, excluding stopwords)."""
    return {t for t in norm(s).split() if len(t) > 2 and t not in STOPWORDS}


def as_list(value) -> list:
    """XML-to-JSON gives a bare dict for single children; normalize to list."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def to_float(value):
    try:
        return float(value)
    except TypeError, ValueError:
        return None


def parse_ste_chars(text: str) -> dict:
    """Parse pipe-separated STE characteristics into {name: {value, unit}}."""
    out = {}
    if not isinstance(text, str):
        return out
    for part in text.split("|"):
        part = part.strip()
        if not part:
            continue
        m = TAIL_RE.search(part)
        if m and part[: m.start()].strip():
            out[norm(part[: m.start()])] = {
                "value": float(m.group(1).replace(",", ".")),
                "unit": m.group(2).lower(),
            }
        else:
            out[norm(part)] = {"value": None, "unit": ""}
    return out


def parse_ktru_chars(block: dict) -> dict:
    """Parse KTRU characteristics JSON into {name: {required, values}}."""
    out = {}
    for c in as_list((block or {}).get("characteristic")):
        values = []
        for v in as_list((c.get("values") or {}).get("value")):
            rs = (v.get("rangeSet") or {}).get("valueRange")
            if rs:
                values.append(("range", to_float(rs.get("min")), to_float(rs.get("max"))))
            else:
                values.append(("enum", v.get("valueCode")))
        out[norm(c.get("name", ""))] = {
            "required": c.get("isRequired") == "true",
            "values": values,
        }
    return out


def read_ste(path: Path) -> pd.DataFrame:
    """Read the first sheet of an xlsx file as all-string, no NaN."""
    return pd.read_excel(path, sheet_name=0, dtype=str).fillna("")


def load_ste(df: pd.DataFrame) -> list[dict]:
    """Parse STE dataframe rows into typed dicts."""
    out = []
    for _, row in df.iterrows():
        name = row.get("Наименование СТЕ", "")
        chars_raw = row.get("Характеристики", "")
        out.append(
            {
                "id": row.get("ID СТЕ", ""),
                "name": name,
                "norm": norm(name),
                "tokens": tokens(name),
                "chars_raw": chars_raw,
                "chars": parse_ste_chars(chars_raw),
                "manufacturer": row.get("Производитель", ""),
                "country": row.get("Страна происхождения", ""),
            }
        )
    return out


def read_json(path: Path):
    """Read plain JSON or gzip/JSONL (one JSON object per line)."""
    data = path.read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    text = data.decode("utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]


def load_ktru(path: Path) -> list[dict]:
    """Read KTRU records from JSON (single object, list, or {items: [...]})."""
    raw = read_json(path)
    if isinstance(raw, dict) and "data" in raw:
        items = [raw]
    elif isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict) and "items" in raw:
        items = raw["items"]
    else:
        items = [raw]

    out = []
    for it in items:
        d = it.get("data", it)
        name = d.get("name", "")
        out.append(
            {
                "code": d.get("code", ""),
                "name": name,
                "norm": norm(name),
                "tokens": tokens(name),
                "chars": parse_ktru_chars(d.get("characteristics")),
                "okpd2": (d.get("OKPD2") or {}).get("code", ""),
            }
        )
    return out


def build_index(ktru: list[dict]) -> dict[str, list[int]]:
    """Inverted index: token -> KTRU indices."""
    index: dict[str, list[int]] = {}
    for i, k in enumerate(ktru):
        for t in k["tokens"]:
            index.setdefault(t, []).append(i)
    return index


def name_score(a: str, b: str) -> float:
    """Combined token-set + sequence similarity in [0, 100]."""
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    jaccard = len(ta & tb) / len(ta | tb)
    seq = SequenceMatcher(None, a, b).ratio()
    return 100.0 * (0.5 * jaccard + 0.5 * seq)


def blocking(ste: list[dict], ktru: list[dict], min_score: float, top_k: int) -> list[dict]:
    """Find candidate (STE, KTRU) pairs via token overlap + fuzzy name score."""
    index = build_index(ktru)
    pairs: list[dict] = []
    for s in ste:
        if not s["tokens"]:
            continue
        votes: dict[int, int] = {}
        for t in s["tokens"]:
            for i in index.get(t, ()):
                votes[i] = votes.get(i, 0) + 1
        if not votes:
            continue
        for i, overlap in sorted(votes.items(), key=lambda x: -x[1])[:top_k]:
            k = ktru[i]
            score = name_score(s["norm"], k["norm"])
            if score >= min_score:
                pairs.append(
                    {
                        "ktru_code": k["code"],
                        "ktru_name": k["name"],
                        "ste_id": s["id"],
                        "ste_name": s["name"],
                        "score": round(score, 2),
                        "token_overlap": overlap,
                    }
                )
    return pairs


def best_ktru(pairs: list[dict]) -> dict[str, str]:
    """Best KTRU code per STE id: highest score, ties broken by overlap then code."""
    best: dict[str, tuple] = {}
    for p in pairs:
        rank = (p["score"], p["token_overlap"], p["ktru_code"])
        if p["ste_id"] not in best or rank > best[p["ste_id"]][0]:
            best[p["ste_id"]] = (rank, p["ktru_code"])
    return {ste_id: code for ste_id, (_, code) in best.items()}


def main() -> None:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ste", required=True, type=Path, help="xlsx with STE rows (first sheet)")
    ap.add_argument("--ktru", required=True, type=Path, help="JSON with KTRU records")
    ap.add_argument(
        "--out",
        required=True,
        type=Path,
        help="output .xlsx (STE+KTRU and pairs) or .csv (STE+KTRU)",
    )
    ap.add_argument("--min-score", type=float, default=60.0, help="fuzzy name threshold 0..100")
    ap.add_argument("--top-k", type=int, default=10, help="max KTRU candidates per STE")
    ap.add_argument(
        "--okpd",
        default="",
        help="comma-separated OKPD2 prefixes to restrict the KTRU catalog (e.g. 25.73,28.24)",
    )
    args = ap.parse_args()

    ste_df = read_ste(args.ste)
    ste = load_ste(ste_df)
    ktru = load_ktru(args.ktru)
    prefixes = tuple(p.strip() for p in args.okpd.split(",") if p.strip())
    if prefixes:
        ktru = [k for k in ktru if k["okpd2"].startswith(prefixes)]
    pairs = blocking(ste, ktru, args.min_score, args.top_k)
    ste_df["Код КТРУ"] = ste_df["ID СТЕ"].map(best_ktru(pairs)).fillna("")

    if args.out.suffix.lower() == ".csv":
        ste_df.to_csv(args.out, index=False)
    else:
        with pd.ExcelWriter(args.out) as w:
            ste_df.to_excel(w, sheet_name="ste", index=False)
            pd.DataFrame(pairs).to_excel(w, sheet_name="pairs", index=False)

    matched = int((ste_df["Код КТРУ"] != "").sum())
    print(
        f"STE: {len(ste)} | KTRU: {len(ktru)} | pairs: {len(pairs)} | "
        f"with KTRU: {matched} -> {args.out}"
    )


if __name__ == "__main__":
    main()
