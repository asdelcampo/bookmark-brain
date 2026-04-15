"""Validate a Block before it is written to disk."""
from __future__ import annotations

from bb.processing.block_generator import Block, VALID_CATEGORIES


class BlockValidationError(ValueError):
    pass


_REQUIRED_NON_EMPTY = ("id", "source_type", "context", "title", "source",
                       "category", "created", "ingested_via", "summary")

_VALID_SOURCE_TYPES = {"x_bookmark", "manual_link", "manual_text"}
_VALID_INGESTED_VIA = {"fieldtheory", "manual_link", "manual_text"}


def validate(block: Block) -> None:
    """Raise BlockValidationError if the block has missing or invalid fields."""
    for attr in _REQUIRED_NON_EMPTY:
        if not getattr(block, attr, None):
            raise BlockValidationError(f"Block {block.id!r}: missing required field '{attr}'")

    if block.source_type not in _VALID_SOURCE_TYPES:
        raise BlockValidationError(
            f"Block {block.id!r}: invalid source_type {block.source_type!r}. "
            f"Must be one of {_VALID_SOURCE_TYPES}"
        )

    if block.category not in VALID_CATEGORIES:
        raise BlockValidationError(
            f"Block {block.id!r}: invalid category {block.category!r}. "
            f"Must be one of {VALID_CATEGORIES}"
        )

    if block.ingested_via not in _VALID_INGESTED_VIA:
        raise BlockValidationError(
            f"Block {block.id!r}: invalid ingested_via {block.ingested_via!r}"
        )

    if not isinstance(block.tags, list):
        raise BlockValidationError(f"Block {block.id!r}: tags must be a list")

    # Validate date format (YYYY-MM-DD)
    import re
    if not re.match(r"\d{4}-\d{2}-\d{2}", block.created):
        raise BlockValidationError(
            f"Block {block.id!r}: 'created' must be ISO date (YYYY-MM-DD), got {block.created!r}"
        )
