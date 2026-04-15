"""Maintenance: dedup by URL + LLM holistic analysis of the knowledge index."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from bb.processing import gemma
from bb.storage import block_store, search_index
from bb.storage.block_store import write_block
from bb.storage.index_manifest import load_index, rebuild_from_blocks


# ---------------------------------------------------------------------------
# Report dataclasses
# ---------------------------------------------------------------------------

@dataclass
class LLMAnalysis:
    tag_suggestions: list[dict]          # [{"id": "...", "add_tags": [...]}]
    potential_dupes: list[list[str]]     # [["id1", "id2"], ...]
    cross_references: list[dict]         # [{"ids": [...], "reason": "..."}]
    gaps: list[str]                      # ["description of gap"]
    summary: str


@dataclass
class HealthReport:
    duplicates_removed: int = 0
    tags_added: int = 0
    analysis: LLMAnalysis | None = None
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Analysis prompt
# ---------------------------------------------------------------------------

_ANALYSIS_SYSTEM = """\
<|think|>
You are analyzing a personal knowledge library. Review the full index of blocks and identify:
1. Blocks that should share tags but don't (suggest tag additions)
2. Potential duplicates (similar context lines)
3. Clusters that could be merged or cross-referenced
4. Gaps: areas with many blocks but missing obvious related topics

Respond with JSON only:
{
  "tag_suggestions": [{"id": "...", "add_tags": ["tag1", "tag2"]}],
  "potential_dupes": [["id1", "id2"]],
  "cross_references": [{"ids": ["id1", "id2"], "reason": "..."}],
  "gaps": ["description of gap"],
  "summary": "overall health assessment in 2-3 sentences"
}\
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_maintenance() -> HealthReport:
    """
    1. Hard-dedup by exact source URL (no LLM).
    2. Run LLM holistic analysis on the full _index.json.

    Tag suggestions are returned in the report but NOT automatically applied —
    the caller (CLI) handles interactive y/n confirmation.
    """
    report = HealthReport()

    # 1. Dedup
    blocks = list(block_store.iter_all_blocks())
    _dedup_by_url(blocks, report)

    # 2. LLM analysis
    index = load_index()
    if index:
        try:
            report.analysis = _run_llm_analysis(index)
        except Exception as exc:
            report.errors.append(f"LLM analysis failed: {exc}")

    # 3. Rebuild manifest to reflect any writes
    rebuild_from_blocks()

    return report


def apply_tag_suggestion(suggestion: dict) -> int:
    """
    Apply a single tag suggestion dict ({"id": "...", "add_tags": [...]}).
    Returns the number of new tags actually added (0 if block not found or
    all tags already present).
    """
    block_id = suggestion.get("id", "")
    new_tags = [str(t).lower().strip() for t in (suggestion.get("add_tags") or [])]
    if not block_id or not new_tags:
        return 0

    block = block_store.read_block(block_id)
    if not block:
        return 0

    existing = set(block.tags)
    additions = [t for t in new_tags if t not in existing]
    if not additions:
        return 0

    block.tags = block.tags + additions
    write_block(block)
    search_index.upsert_block(block)
    return len(additions)


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------

def _dedup_by_url(blocks, report: HealthReport) -> None:
    seen: dict[str, str] = {}  # normalised source -> block_id
    for block in blocks:
        key = (block.source or "").strip().lower()
        if not key or key == "(manual text)":
            continue
        if key in seen:
            existing_id = seen[key]
            # Keep the lexicographically smaller ID (earlier-generated)
            remove_id = block.id if block.id > existing_id else existing_id
            keep_id = existing_id if block.id > existing_id else block.id
            block_store.delete_block(remove_id)
            search_index.remove_block(remove_id)
            seen[key] = keep_id
            report.duplicates_removed += 1
        else:
            seen[key] = block.id


# ---------------------------------------------------------------------------
# LLM holistic analysis
# ---------------------------------------------------------------------------

def _run_llm_analysis(index: list[dict]) -> LLMAnalysis:
    user_message = (
        f"Here is the full block index ({len(index)} blocks):\n\n"
        + json.dumps(index, indent=2)
    )
    messages = [
        {"role": "system", "content": _ANALYSIS_SYSTEM},
        {"role": "user", "content": user_message},
    ]
    response = gemma.chat(messages, temperature=0.5, num_predict=2048)
    data = _parse_json(response)

    return LLMAnalysis(
        tag_suggestions=data.get("tag_suggestions") or [],
        potential_dupes=data.get("potential_dupes") or [],
        cross_references=data.get("cross_references") or [],
        gaps=data.get("gaps") or [],
        summary=data.get("summary", ""),
    )


# ---------------------------------------------------------------------------
# JSON parsing helper
# ---------------------------------------------------------------------------

def _parse_json(response: str) -> dict:
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", response, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {}
