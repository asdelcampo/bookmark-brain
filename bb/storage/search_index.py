"""SQLite FTS5 search index operations."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from bb.config import DB_PATH
from bb.processing.block_generator import Block


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS blocks_fts (
    id            TEXT PRIMARY KEY,
    context       TEXT,
    title         TEXT,
    summary       TEXT,
    tags          TEXT,
    relevance_hint TEXT,
    category      TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS blocks_search USING fts5(
    id UNINDEXED,
    context,
    title,
    summary,
    tags,
    relevance_hint,
    category,
    content='blocks_fts',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS blocks_ai AFTER INSERT ON blocks_fts BEGIN
    INSERT INTO blocks_search(rowid, id, context, title, summary, tags, relevance_hint, category)
    VALUES (new.rowid, new.id, new.context, new.title, new.summary, new.tags, new.relevance_hint, new.category);
END;

CREATE TRIGGER IF NOT EXISTS blocks_ad AFTER DELETE ON blocks_fts BEGIN
    INSERT INTO blocks_search(blocks_search, rowid, id, context, title, summary, tags, relevance_hint, category)
    VALUES ('delete', old.rowid, old.id, old.context, old.title, old.summary, old.tags, old.relevance_hint, old.category);
END;

CREATE TRIGGER IF NOT EXISTS blocks_au AFTER UPDATE ON blocks_fts BEGIN
    INSERT INTO blocks_search(blocks_search, rowid, id, context, title, summary, tags, relevance_hint, category)
    VALUES ('delete', old.rowid, old.id, old.context, old.title, old.summary, old.tags, old.relevance_hint, old.category);
    INSERT INTO blocks_search(rowid, id, context, title, summary, tags, relevance_hint, category)
    VALUES (new.rowid, new.id, new.context, new.title, new.summary, new.tags, new.relevance_hint, new.category);
END;
"""


@dataclass
class SearchResult:
    block_id: str
    title: str
    context: str
    category: str
    tags: list[str]
    rank: float


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

@contextmanager
def _conn(path: Path = DB_PATH):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    with _conn() as con:
        for statement in _DDL.strip().split(";\n\n"):
            s = statement.strip()
            if s:
                con.execute(s)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def upsert_block(block: Block) -> None:
    tags_str = " ".join(block.tags)
    with _conn() as con:
        existing = con.execute(
            "SELECT id FROM blocks_fts WHERE id = ?", (block.id,)
        ).fetchone()
        if existing:
            con.execute(
                """UPDATE blocks_fts
                   SET context=?, title=?, summary=?, tags=?, relevance_hint=?, category=?
                   WHERE id=?""",
                (block.context, block.title, block.summary, tags_str,
                 block.relevance_hint, block.category, block.id),
            )
        else:
            con.execute(
                """INSERT INTO blocks_fts(id, context, title, summary, tags, relevance_hint, category)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (block.id, block.context, block.title, block.summary,
                 tags_str, block.relevance_hint, block.category),
            )


def remove_block(block_id: str) -> None:
    with _conn() as con:
        con.execute("DELETE FROM blocks_fts WHERE id = ?", (block_id,))


def _fts5_query(raw: str) -> str:
    """Convert a natural-language string into a safe FTS5 query expression.

    Each word becomes an independent term joined with OR so that partial
    matches still surface results. Special FTS5 characters are stripped
    to avoid syntax errors.
    """
    import re
    # Strip characters that have FTS5 query meaning
    cleaned = re.sub(r'[^\w\s]', ' ', raw)
    words = [w for w in cleaned.split() if len(w) > 1]
    if not words:
        return '""'  # empty phrase match returns nothing gracefully
    return " OR ".join(words)


def search(query: str, limit: int = 15) -> list[SearchResult]:
    """FTS5 full-text search. Returns up to *limit* results sorted by rank."""
    fts_query = _fts5_query(query)
    with _conn() as con:
        rows = con.execute(
            """SELECT b.id, b.title, b.context, b.category, b.tags,
                      s.rank
               FROM blocks_search s
               JOIN blocks_fts b ON b.id = s.id
               WHERE blocks_search MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (fts_query, limit),
        ).fetchall()
    return [
        SearchResult(
            block_id=r["id"],
            title=r["title"],
            context=r["context"],
            category=r["category"],
            tags=r["tags"].split() if r["tags"] else [],
            rank=r["rank"],
        )
        for r in rows
    ]


def rebuild_index() -> int:
    """Wipe and rebuild the FTS index from the blocks_fts table."""
    with _conn() as con:
        con.execute("INSERT INTO blocks_search(blocks_search) VALUES ('rebuild')")
    return 0
