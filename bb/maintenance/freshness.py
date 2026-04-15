"""URL liveness checks — flag blocks whose source URLs are dead."""
from __future__ import annotations

from dataclasses import dataclass, field

from bb.ingestion.scraper import check_liveness
from bb.processing.block_generator import Block
from bb.storage import block_store, search_index
from bb.storage.block_store import write_block


@dataclass
class FreshnessReport:
    checked: int = 0
    alive: int = 0
    dead: int = 0
    skipped: int = 0
    newly_stale: list[str] = field(default_factory=list)   # block IDs marked stale this run
    already_stale: list[str] = field(default_factory=list) # block IDs already flagged


def run_freshness_check(verbose: bool = False) -> FreshnessReport:
    """
    HEAD-check every block that has a real URL.

    Blocks that fail (404, timeout, connection error) get ``stale: true``
    written to their frontmatter.  Blocks already marked stale are
    rechecked — if the URL recovers, the flag is cleared.
    """
    report = FreshnessReport()
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
            if block.stale:
                # URL recovered — clear the flag
                block.stale = False
                write_block(block)
                search_index.upsert_block(block)
                if verbose:
                    print(f"  ALIVE (recovered)  {block.id}: {url}")
        else:
            report.dead += 1
            if block.stale:
                report.already_stale.append(block.id)
                if verbose:
                    print(f"  DEAD  (already flagged)  {block.id}: {url}")
            else:
                _mark_stale(block)
                report.newly_stale.append(block.id)
                if verbose:
                    print(f"  DEAD  (newly flagged)  {block.id}: {url}")

    return report


def _mark_stale(block: Block) -> None:
    """Set ``stale: true`` in the block's frontmatter."""
    block.stale = True
    write_block(block)
    search_index.upsert_block(block)
