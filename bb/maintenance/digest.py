"""Generate a 'what's new' digest summary."""
from __future__ import annotations

from datetime import date, timedelta

from bb.processing import gemma
from bb.storage.block_store import iter_all_blocks


def generate_digest(days: int = 7) -> str:
    """Return a Gemma-written summary of blocks added in the last *days* days."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    recent = [
        b for b in iter_all_blocks()
        if b.created >= cutoff
    ]

    if not recent:
        return f"No new blocks in the last {days} days."

    bullet_list = "\n".join(
        f"- [{b.category}] {b.title}: {b.context}"
        for b in sorted(recent, key=lambda b: b.created, reverse=True)
    )

    prompt = (
        f"Here are knowledge blocks added in the last {days} days:\n\n"
        f"{bullet_list}\n\n"
        "Write a concise digest (3-6 sentences) highlighting the most interesting additions "
        "and any emerging themes or clusters."
    )

    return gemma.generate(prompt)
