"""SQLite deck index. One file per project, under `.skimsearch/index.sqlite`."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from skimsearch.errors import SkimError
from skimsearch.norm import normalize_text

INDEX_DIRNAME = ".skimsearch"
INDEX_FILENAME = "index.sqlite"
SCHEMA_VERSION = "1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    mtime REAL NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    language TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    language TEXT NOT NULL,
    kind TEXT NOT NULL,
    symbol TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    part INTEGER NOT NULL DEFAULT 1,
    text TEXT NOT NULL,
    preview TEXT NOT NULL,
    token_estimate INTEGER NOT NULL,
    embedding BLOB
);

CREATE INDEX IF NOT EXISTS idx_cards_path ON cards(path);

CREATE VIRTUAL TABLE IF NOT EXISTS cards_fts USING fts5(
    symbol,
    path,
    preview,
    body,
    tokenize = 'porter unicode61'
);
"""


def index_path(root: Path) -> Path:
    return root / INDEX_DIRNAME / INDEX_FILENAME


def has_index(root: Path) -> bool:
    return index_path(root).is_file()


def resolve_index_root(start: Path) -> Path:
    """Find the nearest indexed project at `start` or one of its parents."""
    current = start.resolve()
    if has_index(current):
        return current
    for parent in current.parents:
        if has_index(parent):
            return parent
    return current


def remove_index(root: Path) -> None:
    path = index_path(root)
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(path) + suffix)
        if candidate.exists():
            candidate.unlink()


def connect(root: Path) -> sqlite3.Connection:
    path = index_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.executescript(SCHEMA)
    current = get_meta(con, "schema_version")
    if current is None:
        set_meta(con, "schema_version", SCHEMA_VERSION)
        con.commit()
    elif current != SCHEMA_VERSION:
        con.close()
        raise SkimError(f"index schema {current} is unsupported; run skimsearch index --force")
    return con


def get_meta(con: sqlite3.Connection, key: str) -> str | None:
    row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    return str(row["value"])


def set_meta(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def delete_path(con: sqlite3.Connection, rel: str) -> None:
    rows = con.execute("SELECT id FROM cards WHERE path = ?", (rel,)).fetchall()
    for row in rows:
        con.execute("DELETE FROM cards_fts WHERE rowid = ?", (int(row["id"]),))
    con.execute("DELETE FROM cards WHERE path = ?", (rel,))
    con.execute("DELETE FROM files WHERE path = ?", (rel,))


def insert_card(
    con: sqlite3.Connection,
    *,
    path: str,
    language: str,
    kind: str,
    symbol: str,
    start_line: int,
    end_line: int,
    part: int,
    text: str,
    preview: str,
    token_estimate: int,
) -> int:
    cur = con.execute(
        """
        INSERT INTO cards (
            path, language, kind, symbol, start_line, end_line, part,
            text, preview, token_estimate, embedding
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (
            path,
            language,
            kind,
            symbol,
            start_line,
            end_line,
            part,
            text,
            preview,
            token_estimate,
        ),
    )
    card_id = int(cur.lastrowid)
    con.execute(
        "INSERT INTO cards_fts(rowid, symbol, path, preview, body) VALUES (?, ?, ?, ?, ?)",
        (
            card_id,
            normalize_text(symbol),
            normalize_text(path),
            normalize_text(preview),
            normalize_text(text),
        ),
    )
    return card_id


def optimize(con: sqlite3.Connection) -> None:
    con.execute("INSERT INTO cards_fts(cards_fts) VALUES('optimize')")


def load_cards(con: sqlite3.Connection, ids: list[int]) -> dict[int, sqlite3.Row]:
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    rows = con.execute(f"SELECT * FROM cards WHERE id IN ({marks})", ids).fetchall()
    return {int(row["id"]): row for row in rows}


def counts(con: sqlite3.Connection) -> tuple[int, int, int]:
    files = int(con.execute("SELECT COUNT(*) AS n FROM files").fetchone()["n"])
    cards = int(con.execute("SELECT COUNT(*) AS n FROM cards").fetchone()["n"])
    tokens = int(
        con.execute("SELECT COALESCE(SUM(token_estimate), 0) AS n FROM cards").fetchone()["n"]
    )
    return files, cards, tokens
