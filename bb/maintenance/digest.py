"""Generate a conversational 'what's new' digest grouped by category."""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from bb.processing import gemma
from bb.storage.block_store import iter_all_blocks


def generate_digest(days: int = 7) -> str:
    """
    Return a conversational LLM-generated summary of blocks added in the last
    *days* days, grouped by category.

    The prompt asks the model to open with a count sentence and highlight the
    single most interesting addition.
    """
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    recent = sorted(
        [b for b in iter_all_blocks() if b.created >= cutoff],
        key=lambda b: b.created,
        reverse=True,
    )

    if not recent:
        return f"No new blocks in the last {days} days."

    # Group by category for the prompt
    by_category: dict[str, list] = {}
    for b in recent:
        by_category.setdefault(b.category, []).append(b)

    category_lines: list[str] = []
    for cat, blocks in sorted(by_category.items()):
        items = "\n".join(f"  - [{b.id}] {b.title}: {b.context}" for b in blocks)
        category_lines.append(f"{cat.upper()} ({len(blocks)}):\n{items}")

    grouped_text = "\n\n".join(category_lines)

    # Build category count phrase, e.g. "3 about AI agents, 2 about design"
    cat_counts = ", ".join(
        f"{len(v)} about {k}" for k, v in sorted(by_category.items(), key=lambda x: -len(x[1]))
    )

    prompt = (
        f"The user bookmarked {len(recent)} new item(s) in the last {days} day(s). "
        f"Here is a breakdown by category:\n\n"
        f"{grouped_text}\n\n"
        f"Write a short, conversational digest (3-5 sentences). "
        f"Open with a sentence like: 'You bookmarked {len(recent)} new items this week. "
        f"{cat_counts.capitalize()}.' "
        f"Then highlight the single most interesting or surprising addition and explain "
        f"briefly why it stands out. Keep the tone natural and personal."
    )

    return gemma.generate(prompt)
