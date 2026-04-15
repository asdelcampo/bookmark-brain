"""Manual ingestion: bb add <url>, bb add --text, bb add --clipboard."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import os
from dataclasses import dataclass

from bb.ingestion.scraper import ScrapedPage, resolve_and_scrape


@dataclass
class ManualInput:
    source_type: str          # "manual_link" | "manual_text"
    raw_text: str             # tweet text / page text / pasted text
    url: str | None = None    # original URL if provided
    resolved_url: str | None = None
    title: str | None = None
    scraped_body: str | None = None
    note: str | None = None   # extra context passed by the user


def ingest_url(url: str, note: str | None = None) -> ManualInput:
    """Fetch and scrape a URL provided by the user."""
    page: ScrapedPage | None = resolve_and_scrape(url)
    if page is None:
        return ManualInput(
            source_type="manual_link",
            raw_text="",
            url=url,
            resolved_url=url,
            title=url,
            note=note,
        )
    return ManualInput(
        source_type="manual_link",
        raw_text=page.text,
        url=url,
        resolved_url=page.url,
        title=page.title,
        scraped_body=page.text,
        note=note,
    )


def ingest_text_from_editor() -> ManualInput:
    """Open $EDITOR (fallback: nano), let user type, return the result."""
    editor = os.getenv("EDITOR", "nano")
    with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w") as tmp:
        tmp.write("# Paste or write your note below\n\n")
        tmp_path = tmp.name
    try:
        subprocess.run([editor, tmp_path], check=True)
        with open(tmp_path) as fh:
            content = fh.read().strip()
    finally:
        os.unlink(tmp_path)

    if not content or content.startswith("# Paste or write"):
        raise ValueError("No content entered.")
    return ManualInput(source_type="manual_text", raw_text=content)


def ingest_text_from_stdin() -> ManualInput:
    """Read text from stdin (pipe mode)."""
    if sys.stdin.isatty():
        raise ValueError("stdin is a TTY — pipe content or use --text to open editor.")
    content = sys.stdin.read().strip()
    if not content:
        raise ValueError("Empty stdin.")
    return ManualInput(source_type="manual_text", raw_text=content)


def ingest_clipboard() -> ManualInput:
    """Read from system clipboard (macOS pbpaste with pyperclip fallback)."""
    content = _read_clipboard()
    if not content or not content.strip():
        raise ValueError("Clipboard is empty.")
    content = content.strip()
    if content.startswith(("http://", "https://")):
        return ingest_url(content)
    return ManualInput(source_type="manual_text", raw_text=content)


def _read_clipboard() -> str:
    """Read clipboard content, preferring pbpaste on macOS."""
    # Try pbpaste first (reliable on macOS, no X11 requirement)
    try:
        result = subprocess.run(
            ["pbpaste"], capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0:
            return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fall back to pyperclip (cross-platform)
    try:
        import pyperclip
        return pyperclip.paste()
    except Exception as exc:
        raise RuntimeError(
            f"Could not read clipboard: {exc}\n"
            "On macOS, pbpaste should be available. On Linux, install xclip or xsel."
        ) from exc
