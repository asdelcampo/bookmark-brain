"""Read and write block Markdown files with YAML frontmatter."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import frontmatter  # python-frontmatter

from bb.config import BLOCKS_DIR, CATEGORY_DIRS, PROCESSED_IDS_PATH
from bb.processing.block_generator import Block


# ---------------------------------------------------------------------------
# Block ID generation  (5-char SHA-256 prefix, extended on collision)
# ---------------------------------------------------------------------------

def generate_id(source: str, existing: set[str] | None = None) -> str:
    """
    Return a short unique block ID derived from *source*.

    *source* should be the bookmark URL when available, otherwise the first
    500 characters of raw content.  The ID is the shortest leading hex slice
    of sha256(*source*) that does not collide with *existing* IDs.  The
    minimum length is 5 characters.

    If *existing* is not supplied, currently-indexed IDs are loaded from
    _index.json automatically.
    """
    if existing is None:
        from bb.storage.index_manifest import load_index
        existing = {e["id"] for e in load_index()}

    digest = hashlib.sha256(source.encode()).hexdigest()
    length = 5
    while length <= len(digest):
        candidate = digest[:length]
        if candidate not in existing:
            return candidate
        length += 1
    raise RuntimeError(f"Could not generate a unique ID for source: {source[:60]!r}")


def resolve_block_id(prefix: str) -> str:
    """
    Resolve a user-supplied prefix to a full block ID.

    Returns the matching ID if exactly one block starts with *prefix*.
    Raises ``ValueError`` with the list of matching IDs if the prefix is
    ambiguous, or ``KeyError`` if there are no matches.
    """
    all_ids = [p.stem for p in BLOCKS_DIR.rglob("*.md") if p.stem != "_index"]
    matches = [bid for bid in all_ids if bid.startswith(prefix)]
    if not matches:
        raise KeyError(prefix)
    if len(matches) > 1:
        raise ValueError(matches)
    return matches[0]


# ---------------------------------------------------------------------------
# One-time migration: bkmk_YYYYMMDD_NNN → 5-char hash IDs
# ---------------------------------------------------------------------------

_LEGACY_PATTERN = re.compile(r"^bkmk_\d{8}_\d+$")


def migrate_legacy_ids() -> int:
    """
    Rename legacy bkmk_* block files to 5-char hash IDs in-place.

    Returns the number of files migrated (0 if nothing to do).
    Called automatically by the CLI on startup when legacy files are
    detected — safe to run multiple times.
    """
    legacy_files = [
        p for p in BLOCKS_DIR.rglob("*.md")
        if _LEGACY_PATTERN.match(p.stem)
    ]
    if not legacy_files:
        return 0

    # --- Phase 1: assign new IDs, checking for collisions ----------------
    # Pre-load all existing short IDs so we can track collisions across the
    # batch (existing blocks already migrated + those in the current run).
    from bb.storage.index_manifest import load_index, save_index
    existing_ids: set[str] = {e["id"] for e in load_index()}

    id_map: dict[str, str] = {}  # old_stem → new_id
    for path in legacy_files:
        post = frontmatter.load(path)
        raw_source = post.metadata.get("source") or ""
        if raw_source and raw_source.strip() != "(manual text)":
            source = raw_source
        else:
            # No URL — use context (always unique per block) as the ID seed
            source = (
                post.metadata.get("context")
                or post.metadata.get("title")
                or post.content[:500]
                or path.stem
            )
        new_id = generate_id(source, existing=existing_ids)
        id_map[path.stem] = new_id
        existing_ids.add(new_id)

    # --- Phase 2: rename files and rewrite frontmatter -------------------
    for path in legacy_files:
        old_id = path.stem
        new_id = id_map[old_id]
        post = frontmatter.load(path)
        post.metadata["id"] = new_id
        new_path = path.parent / f"{new_id}.md"
        new_path.write_text(frontmatter.dumps(post), encoding="utf-8")
        if new_path != path:
            path.unlink()

    # --- Phase 3: rebuild index and FTS from the updated files -----------
    from bb.storage.index_manifest import rebuild_from_blocks
    from bb.storage.search_index import upsert_block, remove_block

    # Remove old entries from FTS
    for old_id in id_map:
        remove_block(old_id)

    # Rebuild _index.json and re-index all blocks
    rebuild_from_blocks()
    for md in BLOCKS_DIR.rglob("*.md"):
        try:
            block = read_block_path(md)
            upsert_block(block)
        except Exception:
            pass

    return len(legacy_files)


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _block_to_frontmatter(block: Block) -> frontmatter.Post:
    meta = {
        "id": block.id,
        "source_type": block.source_type,
        "context": block.context,
        "title": block.title,
        "source": block.source,
        "source_resolved": block.source_resolved,
        "tags": block.tags,
        "relevance_hint": block.relevance_hint,
        "category": block.category,
        "created": block.created,
        "tweet_author": block.tweet_author,
        "ingested_via": block.ingested_via,
    }
    return frontmatter.Post(block.summary, **meta)


def _post_to_block(post: frontmatter.Post, file_id: str | None = None) -> Block:
    m = post.metadata
    return Block(
        id=m.get("id") or file_id or "",
        source_type=m.get("source_type", ""),
        context=m.get("context", ""),
        title=m.get("title", ""),
        source=m.get("source", ""),
        source_resolved=m.get("source_resolved"),
        tags=m.get("tags") or [],
        relevance_hint=m.get("relevance_hint", ""),
        category=m.get("category", "other"),
        created=str(m.get("created", "")),
        tweet_author=m.get("tweet_author"),
        ingested_via=m.get("ingested_via", ""),
        summary=post.content,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def write_block(block: Block) -> Path:
    """Serialise *block* to its category subdirectory and return the path."""
    category_dir = CATEGORY_DIRS.get(block.category, CATEGORY_DIRS["other"])
    path = category_dir / f"{block.id}.md"
    post = _block_to_frontmatter(block)
    path.write_text(frontmatter.dumps(post), encoding="utf-8")
    return path


def read_block(block_id: str) -> Block | None:
    """Find and parse a block by ID (exact match), searching all category dirs."""
    for md in BLOCKS_DIR.rglob(f"{block_id}.md"):
        post = frontmatter.load(md)
        return _post_to_block(post, block_id)
    return None


def read_block_path(path: Path) -> Block:
    post = frontmatter.load(path)
    return _post_to_block(post, path.stem)


def iter_all_blocks():
    """Yield every Block in the blocks/ tree."""
    for md in sorted(BLOCKS_DIR.rglob("*.md")):
        if md.name == "_index.json":
            continue
        try:
            yield read_block_path(md)
        except Exception:
            pass


def delete_block(block_id: str) -> bool:
    for md in BLOCKS_DIR.rglob(f"{block_id}.md"):
        md.unlink()
        return True
    return False


# ---------------------------------------------------------------------------
# Processed-ID tracking (dedup for ft sync)
# ---------------------------------------------------------------------------

def load_processed_ids() -> set[str]:
    if PROCESSED_IDS_PATH.exists():
        return set(json.loads(PROCESSED_IDS_PATH.read_text()))
    return set()


def save_processed_ids(ids: set[str]) -> None:
    PROCESSED_IDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROCESSED_IDS_PATH.write_text(json.dumps(sorted(ids), indent=2))


def mark_processed(bookmark_id: str) -> None:
    ids = load_processed_ids()
    ids.add(bookmark_id)
    save_processed_ids(ids)
