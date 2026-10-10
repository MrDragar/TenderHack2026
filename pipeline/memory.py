import re
import sqlite3
import unicodedata
from contextlib import closing
from pathlib import Path
from threading import Lock

DEFAULT_MEMORY_PATH = Path("data/runtime/learned.sqlite3")
BUSY_TIMEOUT_MS = 5_000
CONTEXT_KINDS = {"ktru_family", "portal_category", "free_product"}
VALUE_TYPES = {"text", "number", "boolean"}

_INITIALIZED: set[Path] = set()
_INITIALIZE_LOCK = Lock()


def normalize_key(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    return " ".join(re.sub(r"[^\w]+", " ", normalized).replace("_", " ").split())


def load_all(path: str | Path = DEFAULT_MEMORY_PATH) -> list[dict]:
    database = _database_path(path)
    if not database.exists():
        return []
    _initialize(database)
    with closing(_connect(database)) as connection:
        rows = connection.execute(
            """
            SELECT id, context_kind, context_key, name, value_type, unit_code, unit_name,
                   confirmations
            FROM learned_characteristics
            ORDER BY context_kind, context_key, name_key
            """
        ).fetchall()
        aliases: dict[int, list[str]] = {}
        for row in connection.execute(
            """
            SELECT characteristic_id, alias
            FROM learned_aliases
            ORDER BY characteristic_id, alias_key
            """
        ):
            aliases.setdefault(row["characteristic_id"], []).append(row["alias"])
    return [_as_dict(row, aliases.get(row["id"], [])) for row in rows]


def save_characteristic(
    path: str | Path = DEFAULT_MEMORY_PATH, proposal: dict | None = None
) -> dict:
    if proposal is None:
        raise TypeError("proposal is required")

    item = _validated(proposal)
    database = _database_path(path)
    _initialize(database)
    with closing(_connect(database)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = connection.execute(
                """
                SELECT id
                FROM learned_characteristics
                WHERE context_kind = ? AND context_key = ? AND name_key = ?
                """,
                (item["context_kind"], item["context_key"], item["name_key"]),
            ).fetchone()
            action = "updated" if existing else "created"
            connection.execute(
                """
                INSERT INTO learned_characteristics (
                    context_kind, context_key, name, name_key, value_type, unit_code, unit_name,
                    confirmations
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(context_kind, context_key, name_key) DO UPDATE SET
                    name = excluded.name,
                    value_type = excluded.value_type,
                    unit_code = excluded.unit_code,
                    unit_name = excluded.unit_name,
                    confirmations = learned_characteristics.confirmations + 1
                """,
                (
                    item["context_kind"],
                    item["context_key"],
                    item["name"],
                    item["name_key"],
                    item["value_type"],
                    item["unit_code"],
                    item["unit_name"],
                ),
            )
            row = connection.execute(
                """
                SELECT id, context_kind, context_key, name, value_type, unit_code, unit_name,
                       confirmations
                FROM learned_characteristics
                WHERE context_kind = ? AND context_key = ? AND name_key = ?
                """,
                (item["context_kind"], item["context_key"], item["name_key"]),
            ).fetchone()
            for alias, alias_key in item["aliases"]:
                connection.execute(
                    """
                    INSERT INTO learned_aliases (characteristic_id, alias, alias_key)
                    VALUES (?, ?, ?)
                    ON CONFLICT(characteristic_id, alias_key) DO NOTHING
                    """,
                    (row["id"], alias, alias_key),
                )
            saved_aliases = [
                alias_row["alias"]
                for alias_row in connection.execute(
                    """
                    SELECT alias
                    FROM learned_aliases
                    WHERE characteristic_id = ?
                    ORDER BY alias_key
                    """,
                    (row["id"],),
                )
            ]
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return {**_as_dict(row, saved_aliases), "action": action}


def _validated(proposal: dict) -> dict:
    context_kind = _text(proposal, "context_kind")
    context_key = _text(proposal, "context_key")
    name = _text(proposal, "name")
    value_type = _text(proposal, "value_type")
    if context_kind not in CONTEXT_KINDS:
        raise ValueError(f"context_kind must be one of {sorted(CONTEXT_KINDS)}")
    if value_type not in VALUE_TYPES:
        raise ValueError(f"value_type must be one of {sorted(VALUE_TYPES)}")
    unit_code = _optional_text(proposal, "unit_code")
    unit_name = _optional_text(proposal, "unit_name")
    if (unit_code is None) != (unit_name is None):
        raise ValueError("unit_code and unit_name must be both set or both null")
    name_key = normalize_key(name)
    if not name_key:
        raise ValueError("name must contain letters or digits")

    raw_aliases = proposal.get("aliases")
    if not isinstance(raw_aliases, list):
        raise ValueError("aliases must be a list")
    aliases = []
    seen = {name_key}
    for alias in raw_aliases:
        if not isinstance(alias, str) or not alias.strip():
            raise ValueError("aliases must contain non-empty strings")
        alias = alias.strip()
        alias_key = normalize_key(alias)
        if not alias_key:
            raise ValueError("alias must contain letters or digits")
        if alias_key not in seen:
            aliases.append((alias, alias_key))
            seen.add(alias_key)

    return {
        "context_kind": context_kind,
        "context_key": context_key,
        "name": name,
        "name_key": name_key,
        "value_type": value_type,
        "unit_code": unit_code,
        "unit_name": unit_name,
        "aliases": aliases,
    }


def _text(proposal: dict, field: str) -> str:
    value = proposal.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _optional_text(proposal: dict, field: str) -> str | None:
    value = proposal.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be null or a non-empty string")
    return value.strip()


def _as_dict(row: sqlite3.Row, aliases: list[str]) -> dict:
    unit = None
    if row["unit_code"] is not None:
        unit = {"code": row["unit_code"], "name": row["unit_name"]}
    return {
        "id": row["id"],
        "context_kind": row["context_kind"],
        "context_key": row["context_key"],
        "name": row["name"],
        "value_type": row["value_type"],
        "unit": unit,
        "aliases": aliases,
        "confirmations": row["confirmations"],
    }


def _database_path(path: str | Path) -> Path:
    return Path(path).resolve()


def _initialize(path: Path) -> None:
    with _INITIALIZE_LOCK:
        if path in _INITIALIZED and path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1_000)) as connection:
            connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS learned_characteristics (
                    id INTEGER PRIMARY KEY,
                    context_kind TEXT NOT NULL,
                    context_key TEXT NOT NULL,
                    name TEXT NOT NULL,
                    name_key TEXT NOT NULL,
                    value_type TEXT NOT NULL,
                    unit_code TEXT,
                    unit_name TEXT,
                    confirmations INTEGER NOT NULL DEFAULT 1,
                    CHECK(context_kind IN ('ktru_family', 'portal_category', 'free_product')),
                    CHECK(value_type IN ('text', 'number', 'boolean')),
                    CHECK((unit_code IS NULL) = (unit_name IS NULL)),
                    UNIQUE(context_kind, context_key, name_key)
                );

                CREATE TABLE IF NOT EXISTS learned_aliases (
                    characteristic_id INTEGER NOT NULL
                        REFERENCES learned_characteristics(id) ON DELETE CASCADE,
                    alias TEXT NOT NULL,
                    alias_key TEXT NOT NULL,
                    PRIMARY KEY(characteristic_id, alias_key)
                );
                """
            )
        _INITIALIZED.add(path)


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1_000, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection
