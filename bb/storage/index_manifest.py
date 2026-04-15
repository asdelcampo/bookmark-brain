"""Maintain _index.json — a flat array of lightweight block stubs."""
from __future__ import annotations

import json
from pathlib import Path

from bb.config import INDEX_PATH
from bb.processing.block_generator import Block


def _stub(block: Block) -> dict:
    return {
        "id": block.id,
        "context": block.context,
        "tags": block.tags,
        "category": block.category,
    }


def load_index() -> list[dict]:
    if INDEX_PATH.exists():
        try:
            return json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
    return []


def save_index(entries: list[dict]) -> None:
    INDEX_PATH.write_text(
        json.dumps(entries, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def add_or_update(block: Block) -> None:
    entries = load_index()
    # Remove existing entry for this ID
    entries = [e for e in entries if e.get("id") != block.id]
    entries.append(_stub(block))
    save_index(entries)


def remove(block_id: str) -> None:
    entries = [e for e in load_index() if e.get("id") != block_id]
    save_index(entries)


def rebuild_from_blocks() -> int:
    """Regenerate _index.json by scanning all block files."""
    from bb.storage.block_store import iter_all_blocks

    entries = [_stub(b) for b in iter_all_blocks()]
    save_index(entries)
    return len(entries)
