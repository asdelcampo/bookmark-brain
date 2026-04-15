"""Re-cluster, deduplicate, and flag stale blocks via Gemma analysis."""
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
class GemmaAnalysis:
    tag_suggestions: list[dict]          # [{"id": "...", "add_tags": [...]}]
    potential_dupes: list[list[str]]     # [["id1", "id2"], ...]
    cross_references: list[dict]         # [{"ids": [...], "reason": "..."}]
    gaps: list[str]                      # ["description of gap"]
    summary: str


@dataclass
class HealthReport:
    duplicates_removed: int = 0
    tags_added: int = 0
    reclustered: int = 0
    analysis: GemmaAnalysis | None = None
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Maintenance prompt
# ---------------------------------------------------------------------------

_ANALYSIS_SYSTEM = """\
<|think|>
You are analyzing a personal knowledge library. Review the full index of blocks and identify:
1. Blocks that should share tags but don't (suggest tag additions)
2. Potential duplicates (similar context lines)
3. Clusters that could be merged or cross-referenced
4. Gaps: areas with many blocks but missing obvious related topics
Respond with JSON:
{
  "tag_suggestions": [{"id": "...", "add_tags": [...]}],
  "potential_dupes": [["id1", "id2"]],
  "cross_references": [{"ids": [...], "reason": "..."}],
  "gaps": ["description of gap"],
  "summary": "overall health assessment in 2-3 sentences"
}\
"""

_RECATEGORY_SYSTEM = """\
<|think|>
You are a knowledge librarian. Given a single block's metadata, reply with ONLY one word \
for its best category: tools, methods, articles, resources, or other.\
"""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_maintenance() -> HealthReport:
    report = HealthReport()
    blocks = list(block_store.iter_all_blocks())

    # 1. Hard-dedup by exact source URL
    _dedup_by_url(blocks, report)

    # 2. Re-cluster categories one block at a time
    blocks = list(block_store.iter_all_blocks())  # refresh after dedup
    _recluster_categories(blocks, report)

    # 3. Gemma holistic analysis over _index.json
    index = load_index()
    if index:
        try:
            analysis = _run_gemma_analysis(index)
            report.analysis = analysis
            _apply_tag_suggestions(analysis.tag_suggestions, report)
        except Exception as exc:
            report.errors.append(f"Gemma analysis failed: {exc}")

    # 4. Rebuild manifest to reflect any writes
    rebuild_from_blocks()

    return report


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
            # Keep the older block (lexicographically smaller ID = earlier date)
            remove_id = block.id if block.id > existing_id else existing_id
            keep_id = existing_id if block.id > existing_id else block.id
            block_store.delete_block(remove_id)
            search_index.remove_block(remove_id)
            seen[key] = keep_id
            report.duplicates_removed += 1
        else:
            seen[key] = block.id


# ---------------------------------------------------------------------------
# Category re-clustering (per-block, single-word reply)
# ---------------------------------------------------------------------------

def _recluster_categories(blocks, report: HealthReport) -> None:
    from bb.processing.block_generator import VALID_CATEGORIES

    for block in blocks:
        try:
            user_msg = (
                f"Title: {block.title}\n"
                f"Context: {block.context}\n"
                f"Summary: {block.summary}"
            )
            messages = [
                {"role": "system", "content": _RECATEGORY_SYSTEM},
                {"role": "user", "content": user_msg},
            ]
            response = gemma.chat(messages, temperature=0.1, num_predict=8).strip().lower()
            suggested = next((w for w in response.split() if w in VALID_CATEGORIES), None)
            if suggested and suggested != block.category:
                block.category = suggested
                write_block(block)
                search_index.upsert_block(block)
                report.reclustered += 1
        except Exception as exc:
            report.errors.append(f"{block.id}: recluster error — {exc}")


# ---------------------------------------------------------------------------
# Gemma holistic analysis
# ---------------------------------------------------------------------------

def _run_gemma_analysis(index: list[dict]) -> GemmaAnalysis:
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

    return GemmaAnalysis(
        tag_suggestions=data.get("tag_suggestions") or [],
        potential_dupes=data.get("potential_dupes") or [],
        cross_references=data.get("cross_references") or [],
        gaps=data.get("gaps") or [],
        summary=data.get("summary", ""),
    )


# ---------------------------------------------------------------------------
# Apply tag suggestions from analysis
# ---------------------------------------------------------------------------

def _apply_tag_suggestions(suggestions: list[dict], report: HealthReport) -> None:
    for suggestion in suggestions:
        block_id = suggestion.get("id", "")
        new_tags = [str(t).lower().strip() for t in (suggestion.get("add_tags") or [])]
        if not block_id or not new_tags:
            continue
        block = block_store.read_block(block_id)
        if not block:
            continue
        existing = set(block.tags)
        additions = [t for t in new_tags if t not in existing]
        if not additions:
            continue
        block.tags = block.tags + additions
        write_block(block)
        search_index.upsert_block(block)
        report.tags_added += len(additions)


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
