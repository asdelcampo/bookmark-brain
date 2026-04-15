"""Read X/Twitter bookmarks produced by the Field Theory CLI."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from bb.config import FT_BOOKMARKS_PATH


@dataclass
class FTBookmark:
    id: str
    text: str
    created_at: str
    author_username: str
    urls: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, d: dict) -> "FTBookmark":
        # Field Theory CLI schema: links is a flat list of URL strings.
        # Fall back to entities.urls (legacy Twitter API format) if present.
        urls: list[str] = []
        links = d.get("links") or []
        if links:
            urls = [u for u in links if u]
        else:
            for u in (d.get("entities") or {}).get("urls") or []:
                expanded = u.get("expanded_url") or u.get("url") or ""
                if expanded:
                    urls.append(expanded)
        return cls(
            id=str(d["id"]),
            text=d.get("text") or d.get("full_text", ""),
            created_at=d.get("postedAt") or d.get("created_at", ""),
            author_username=d.get("authorHandle") or d.get("author_username") or d.get("user", {}).get("screen_name", ""),
            urls=urls,
            raw=d,
        )


def iter_bookmarks(path: Path = FT_BOOKMARKS_PATH) -> Iterator[FTBookmark]:
    """Yield FTBookmark objects from the .jsonl file, skipping malformed lines."""
    if not path.exists():
        raise FileNotFoundError(
            f"Field Theory bookmarks file not found: {path}\n"
            "Run `ft sync` first, or point BB_FT_PATH at the correct location."
        )
    with path.open() as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                yield FTBookmark.from_dict(d)
            except (json.JSONDecodeError, KeyError) as exc:
                # Don't crash the whole sync for one bad line
                import warnings
                warnings.warn(f"Skipping malformed bookmark at line {lineno}: {exc}")


def load_all(path: Path = FT_BOOKMARKS_PATH) -> list[FTBookmark]:
    return list(iter_bookmarks(path))
