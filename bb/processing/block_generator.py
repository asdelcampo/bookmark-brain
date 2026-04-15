"""Generate a Block dataclass from raw ingestion input using Gemma 4."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date

from bb.processing import gemma

VALID_CATEGORIES = {"tools", "methods", "articles", "resources", "other"}


@dataclass
class Block:
    id: str
    source_type: str          # x_bookmark | manual_link | manual_text
    context: str              # one-line semantic search hint
    title: str
    source: str               # original URL or "(manual text)"
    source_resolved: str | None
    tags: list[str]
    relevance_hint: str
    category: str
    created: str              # ISO date string
    tweet_author: str | None
    ingested_via: str         # fieldtheory | manual_link | manual_text
    summary: str              # 2-4 sentence body


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
<|think|>
You are a knowledge librarian. You receive raw content from a bookmarked tweet and any linked URLs. Your job is to create a structured knowledge block that makes this content findable later.
Respond with ONLY valid JSON, no markdown, no preamble:
{
  "context": "one natural language sentence optimized for search, describing what this is and what problem it solves",
  "title": "short human-readable title",
  "tags": ["3-7 lowercase categorical tags"],
  "relevance_hint": "when would someone need this? describe the situation where this bookmark becomes useful",
  "category": "tools|methods|articles|resources|other",
  "summary": "2-4 sentences: what it is, why it matters, key differentiator from alternatives"
}\
"""

_RETRY_SUFFIX = (
    "\n\nIMPORTANT: Your previous response could not be parsed as JSON. "
    "Reply with ONLY the raw JSON object — no markdown fences, no commentary, "
    "no trailing text. Start your response with { and end with }."
)

_REQUIRED_FIELDS = {"context", "title", "tags", "relevance_hint", "category", "summary"}


def _build_user_message(
    tweet_text: str,
    author: str | None,
    url: str | None,
    scraped_content: str | None,
    note: str | None = None,
) -> str:
    parts: list[str] = []
    if author:
        parts.append(f"Tweet by @{author}:")
    else:
        parts.append("Content:")
    parts.append(tweet_text or "")
    if url and scraped_content:
        parts.append(f"\nLinked content ({url}):")
        parts.append(scraped_content[:3000])
    elif url:
        parts.append(f"\nURL: {url}")
    if note:
        parts.append(f"\nAdditional context from user: {note}")
    return "\n".join(parts)


def _parse_json(response: str) -> dict:
    """Extract and parse the first JSON object from *response*."""
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
    raise ValueError(f"Could not parse JSON from Gemma response:\n{response[:500]}")


def _validate_fields(meta: dict) -> list[str]:
    """Return a list of missing required field names."""
    return [f for f in _REQUIRED_FIELDS if not meta.get(f)]


def _sanitize(meta: dict) -> dict:
    """Normalise field values after successful parse."""
    if meta.get("category") not in VALID_CATEGORIES:
        meta["category"] = "other"
    if not isinstance(meta.get("tags"), list):
        meta["tags"] = []
    meta["tags"] = [str(t).lower().strip() for t in meta["tags"]][:10]
    meta.setdefault("title", "Untitled")
    meta.setdefault("context", (meta.get("summary") or "")[:120])
    meta.setdefault("relevance_hint", "")
    meta.setdefault("summary", "")
    return meta


def _call_with_retry(user_message: str) -> dict:
    """
    Call Gemma via /api/chat with temperature=0.3, num_predict=1024.
    Retry once with a stricter prompt if the first response can't be parsed
    or is missing required fields.
    """
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    response = gemma.chat(messages, temperature=0.3, num_predict=1024)

    try:
        meta = _parse_json(response)
        missing = _validate_fields(meta)
        if not missing:
            return meta
        retry_note = f"Missing fields: {', '.join(missing)}. " + _RETRY_SUFFIX
    except ValueError:
        retry_note = _RETRY_SUFFIX

    # Retry: append assistant's bad response and a correction nudge
    messages.append({"role": "assistant", "content": response})
    messages.append({"role": "user", "content": retry_note})

    response2 = gemma.chat(messages, temperature=0.1, num_predict=1024)
    meta = _parse_json(response2)  # raises ValueError if still broken
    return meta


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_block(
    *,
    raw_text: str,
    url: str | None,
    resolved_url: str | None,
    source_type: str,
    tweet_author: str | None,
    ingested_via: str,
    block_id: str,
    note: str | None = None,
) -> Block:
    """Call Gemma via chat API to produce metadata, then assemble a Block."""
    scraped_content = raw_text if source_type in ("manual_link", "x_bookmark") else None

    user_message = _build_user_message(
        tweet_text=raw_text,
        author=tweet_author.lstrip("@") if tweet_author else None,
        url=url,
        scraped_content=scraped_content,
        note=note,
    )

    meta = _sanitize(_call_with_retry(user_message))

    return Block(
        id=block_id,
        source_type=source_type,
        context=meta["context"],
        title=meta["title"],
        source=url or "(manual text)",
        source_resolved=resolved_url,
        tags=meta["tags"],
        relevance_hint=meta["relevance_hint"],
        category=meta["category"],
        created=date.today().isoformat(),
        tweet_author=tweet_author,
        ingested_via=ingested_via,
        summary=meta["summary"],
    )
