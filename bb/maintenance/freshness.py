"""URL liveness checks — flag blocks whose source URLs are dead."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from bb.config import STALE_DAYS
from bb.ingestion.scraper import check_liveness
from bb.processing.block_generator import Block
from bb.storage import block_store, search_index
from bb.storage.block_store import write_block


@dataclass
class FreshnessReport:
    checked: int
    alive: int
    dead: int
    skipped: int


def run_freshness_check(verbose: bool = False) -> FreshnessReport:
    report = FreshnessReport(checked=0, alive=0, dead=0, skipped=0)
    blocks = list(block_store.iter_all_blocks())

    for block in blocks:
        url = block.source_resolved or block.source
        if not url or url == "(manual text)" or not url.startswith("http"):
            report.skipped += 1
            continue

        report.checked += 1
        alive = check_liveness(url)
        if alive:
            report.alive += 1
        else:
            report.dead += 1
            _mark_stale(block)
            if verbose:
                print(f"  DEAD  {block.id}: {url}")

    return report


def _mark_stale(block: Block) -> None:
    """Append a [STALE] marker to the context so it shows in searches."""
    if "[STALE]" not in block.context:
        block.context = f"[STALE] {block.context}"
    write_block(block)
    search_index.upsert_block(block)
