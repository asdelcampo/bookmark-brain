"""Two-stage retrieval: pure FTS5 by default, optional LLM reranking via --deep."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from bb.config import FTS_TOP_K
from bb.processing.block_generator import Block
from bb.storage import block_store, search_index


@dataclass
class RankedResult:
    block: Block
    score: int          # 1-based rank (1 = best)
    reason: str         # why this block fits the query (LLM-populated when --deep)
    recommendation: str  # prose paragraph (LLM-populated on best match when --deep)


def ask(query: str, top_k: int = FTS_TOP_K, use_llm: bool = False) -> tuple[list[RankedResult], str]:
    """
    Retrieve blocks for *query*.

    Default (use_llm=False): pure FTS5, instant, no LLM dependency.
    With use_llm=True: FTS5 candidates → LLM reranking + recommendation paragraph.

    Returns (results, recommendation_text).
    """
    candidates = search_index.search(query, limit=top_k)
    if not candidates:
        return [], ""

    blocks: list[Block] = []
    for c in candidates:
        b = block_store.read_block(c.block_id)
        if b:
            blocks.append(b)

    if not blocks:
        return [], ""

    if use_llm:
        return _llm_rerank(query, blocks)

    # Pure FTS5 path — return candidates in rank order with relevance_hint as reason
    results = [
        RankedResult(block=b, score=i + 1, reason=b.relevance_hint, recommendation="")
        for i, b in enumerate(blocks)
    ]
    return results, ""


# ---------------------------------------------------------------------------
# LLM reranking (--deep path)
# ---------------------------------------------------------------------------

_RERANK_SYSTEM = """\
<|think|>
You are a knowledge retrieval assistant. The user has a personal library of bookmarked tools, articles, and techniques. They are asking a question and you need to find the best matches.
You will receive:
1. The user's query
2. A list of candidate blocks (id, context, tags, relevance_hint)
Respond with ONLY valid JSON:
{
  "ranked": ["id1", "id2", "id3"],
  "best_match": {
    "id": "the single best block id",
    "reason": "why this is the best fit for their query"
  },
  "recommendation": "2-3 sentence natural language response, e.g. 'You saved 5 things about frontend. Based on your question about X, Block Y is the best fit because...'"
}\
"""


def _build_rerank_user_message(query: str, blocks: list[Block]) -> str:
    candidates = [
        {
            "id": b.id,
            "context": b.context,
            "tags": b.tags,
            "relevance_hint": b.relevance_hint,
        }
        for b in blocks
    ]
    return (
        f"Query: {query}\n\n"
        f"Candidates:\n{json.dumps(candidates, indent=2)}"
    )


def _llm_rerank(query: str, blocks: list[Block]) -> tuple[list[RankedResult], str]:
    """Call the LLM to rerank *blocks* for *query*. Falls back to FTS order on error."""
    from bb.processing import gemma

    user_message = _build_rerank_user_message(query, blocks)
    messages = [
        {"role": "system", "content": _RERANK_SYSTEM},
        {"role": "user", "content": user_message},
    ]

    try:
        response = gemma.chat(messages, temperature=0.4, num_predict=512)
        data = _parse_json(response)
    except Exception:
        return _fts_fallback(blocks), ""

    block_map = {b.id: b for b in blocks}
    recommendation = data.get("recommendation", "")
    best_match = data.get("best_match") or {}
    best_id = best_match.get("id", "")
    best_reason = best_match.get("reason", "")

    results: list[RankedResult] = []
    for rank, bid in enumerate(data.get("ranked", []), start=1):
        b = block_map.get(bid)
        if b:
            reason = best_reason if bid == best_id else ""
            results.append(RankedResult(
                block=b,
                score=rank,
                reason=reason,
                recommendation=recommendation if bid == best_id else "",
            ))

    if not results:
        return _fts_fallback(blocks), recommendation

    return results, recommendation


def _fts_fallback(blocks: list[Block]) -> list[RankedResult]:
    return [
        RankedResult(block=b, score=i + 1, reason=b.relevance_hint, recommendation="")
        for i, b in enumerate(blocks)
    ]


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
