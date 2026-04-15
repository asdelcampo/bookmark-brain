"""'Best fit' recommender for related-block queries."""
from __future__ import annotations

from bb.processing.block_generator import Block
from bb.storage import block_store
from bb.storage.index_manifest import load_index


def find_related(block_id: str, top_k: int = 5, use_llm: bool = False) -> list[Block]:
    """
    Find blocks related to *block_id*.

    Default (use_llm=False): pure FTS5 on source block's tags + context —
    instant, no LLM dependency.

    With use_llm=True: FTS5 candidates → LLM filtering (--deep path).
    """
    from bb.storage.search_index import search

    source = block_store.read_block(block_id)
    if source is None:
        return []

    query_terms = " ".join(source.tags) + " " + source.context
    candidates_raw = search(query_terms, limit=20)

    blocks: list[Block] = []
    for c in candidates_raw:
        if c.block_id == block_id:
            continue
        b = block_store.read_block(c.block_id)
        if b:
            blocks.append(b)

    if not blocks:
        return []

    if use_llm:
        return _llm_filter_related(source, blocks, top_k)

    return blocks[:top_k]


def _llm_filter_related(source: Block, candidates: list[Block], top_k: int) -> list[Block]:
    from bb.processing import gemma
    import json, re

    summaries = "\n\n".join(
        f"ID: {b.id}\nTitle: {b.title}\nContext: {b.context}"
        for b in candidates
    )
    prompt = (
        f"Source block:\n"
        f"Title: {source.title}\nContext: {source.context}\nTags: {', '.join(source.tags)}\n\n"
        f"Candidate blocks:\n{summaries}\n\n"
        f"Return ONLY a JSON array of the IDs of the {top_k} most related candidates, "
        f"ordered by relevance: [\"id1\", \"id2\", ...]"
    )

    try:
        response = gemma.generate(prompt)
        try:
            ids = json.loads(response)
        except json.JSONDecodeError:
            match = re.search(r"\[.*\]", response, re.DOTALL)
            ids = json.loads(match.group()) if match else []

        block_map = {b.id: b for b in candidates}
        return [block_map[i] for i in ids if i in block_map][:top_k]
    except Exception:
        return candidates[:top_k]


def best_fit_for_intent(intent: str) -> list[Block]:
    """
    For very vague queries, load the full index, filter by LLM,
    and return the best-fit blocks.  Not wired to a CLI command.
    """
    from bb.processing import gemma
    import json, re

    index = load_index()
    if not index:
        return []

    compact = "\n".join(
        f"{e['id']}: {e['context']} [{', '.join(e['tags'])}]"
        for e in index
    )
    prompt = (
        f"User intent: {intent}\n\n"
        f"Knowledge index (id: context [tags]):\n{compact[:6000]}\n\n"
        "Return ONLY a JSON array of the 5 most relevant block IDs: [\"id1\", ...]"
    )

    try:
        response = gemma.generate(prompt)
        try:
            ids = json.loads(response)
        except json.JSONDecodeError:
            match = re.search(r"\[.*\]", response, re.DOTALL)
            ids = json.loads(match.group()) if match else []

        results = []
        for bid in ids:
            b = block_store.read_block(bid)
            if b:
                results.append(b)
        return results
    except Exception:
        return []
