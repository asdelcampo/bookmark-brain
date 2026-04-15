"""Click CLI entry point — wires all bb commands together."""
from __future__ import annotations

import subprocess
import sys
from datetime import date, timedelta

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)

from bb.config import ensure_dirs

console = Console()


def _init():
    ensure_dirs()
    from bb.storage.search_index import init_db
    init_db()
    # One-time migration: rename legacy bkmk_* IDs to short hash IDs
    from bb.storage.block_store import migrate_legacy_ids
    migrated = migrate_legacy_ids()
    if migrated:
        console.print(f"[dim]Migrated {migrated} block(s) to short IDs.[/dim]")


# ---------------------------------------------------------------------------
# Custom group — catches LLMError app-wide and prints a friendly message
# ---------------------------------------------------------------------------

class _BB(click.Group):
    def invoke(self, ctx: click.Context):
        from bb.processing.gemma import LLMConnectionError, LLMHTTPError, LLMError
        try:
            return super().invoke(ctx)
        except LLMConnectionError as exc:
            console.print(f"\n[red bold]llama-server not reachable[/red bold] — {exc}")
            console.print(
                "[yellow]→ Start llama-server:[/yellow] [bold]llama-server --port 8001 -m model.gguf[/bold]"
            )
            sys.exit(1)
        except LLMHTTPError as exc:
            console.print(f"\n[red bold]llama-server error[/red bold] — {exc}")
            sys.exit(1)
        except LLMError as exc:
            console.print(f"\n[red bold]LLM error[/red bold] — {exc}")
            sys.exit(1)


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------

@click.group(cls=_BB)
@click.option(
    "--model", default=None, envvar="BB_MODEL",
    help="Override LLM model (e.g. gemma-4-e2b).",
)
@click.pass_context
def cli(ctx: click.Context, model: str | None):
    """Bookmark Brain — local-first knowledge retrieval."""
    if model:
        import bb.config as _cfg
        _cfg.LLM_MODEL = model
    _init()


# ---------------------------------------------------------------------------
# bb sync
# ---------------------------------------------------------------------------

@cli.command()
def sync():
    """Run `ft sync`, then process new bookmarks."""
    console.print("[bold]Running ft sync…[/bold]")
    result = subprocess.run(["ft", "sync"], capture_output=False)
    if result.returncode != 0:
        console.print(
            "[red]ft sync failed.[/red] "
            "Check that [bold]ft[/bold] is installed and you're logged in."
        )
        console.print("[dim]Continuing with locally cached bookmarks…[/dim]")

    ctx = click.get_current_context()
    ctx.invoke(process)


# ---------------------------------------------------------------------------
# bb process
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--limit", default=None, type=int, help="Max bookmarks to process this run.")
def process(limit: int | None):
    """Process queued bookmarks (skip ft sync step)."""
    from bb.ingestion.fieldtheory import iter_bookmarks
    from bb.ingestion.scraper import resolve_and_scrape
    from bb.processing.block_generator import generate_block
    from bb.processing.validator import validate
    from bb.storage.block_store import (
        generate_id, load_processed_ids, mark_processed, write_block,
    )
    from bb.storage.search_index import upsert_block
    from bb.storage.index_manifest import add_or_update, load_index

    processed = load_processed_ids()
    new_count = 0
    error_count = 0

    try:
        bookmarks = list(iter_bookmarks())
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        console.print("[dim]Run [bold]bb sync[/bold] or [bold]ft sync[/bold] first.[/dim]")
        return

    pending = [b for b in bookmarks if b.id not in processed]
    if not pending:
        console.print("Nothing new to process.")
        return

    if limit:
        pending = pending[:limit]

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    ) as progress:
        task = progress.add_task(
            f"Processing [bold]{len(pending)}[/bold] bookmarks", total=len(pending)
        )

        # Pre-load existing IDs once; update incrementally to avoid repeated I/O
        existing_ids: set[str] = {e["id"] for e in load_index()}

        for bm in pending:
            url = bm.urls[0] if bm.urls else None
            resolved_url = url
            raw_text = bm.text
            scrape_note: str | None = None

            if url:
                page = resolve_and_scrape(url)
                if page:
                    raw_text = page.text
                    resolved_url = page.url
                else:
                    scrape_note = f"URL could not be scraped: {url}"

            id_source = url or (bm.text or "")[:500]
            block_id = generate_id(id_source, existing=existing_ids)
            existing_ids.add(block_id)

            try:
                from bb.processing.gemma import LLMConnectionError as _ConnErr
                block = generate_block(
                    raw_text=raw_text or bm.text,
                    url=url,
                    resolved_url=resolved_url,
                    source_type="x_bookmark",
                    tweet_author=f"@{bm.author_username}" if bm.author_username else None,
                    ingested_via="fieldtheory",
                    block_id=block_id,
                    note=scrape_note,
                )
                validate(block)
                write_block(block)
                upsert_block(block)
                add_or_update(block)
                mark_processed(bm.id)
                new_count += 1
                note_flag = " [dim][scrape_failed][/dim]" if scrape_note else ""
                progress.console.print(
                    f"  [green]✓[/green] {block.id} — {block.title[:55]}{note_flag}"
                )
            except _ConnErr:
                raise  # llama-server is down — abort and let global handler explain
            except Exception as exc:
                error_count += 1
                progress.console.print(f"  [red]✗[/red] {bm.id}: {exc}")

            progress.advance(task)

    console.print(
        f"\n[bold]Done.[/bold] {new_count} blocks created, {error_count} errors."
    )


# ---------------------------------------------------------------------------
# bb add
# ---------------------------------------------------------------------------

@cli.command("add")
@click.argument("url", required=False)
@click.option("--text", "mode", flag_value="text", help="Open $EDITOR for text input.")
@click.option("--clipboard", "mode", flag_value="clipboard", help="Ingest from clipboard.")
@click.option("--note", default=None, help="Extra context passed to the LLM for richer block generation.")
def add(url: str | None, mode: str | None, note: str | None):
    """Manually ingest a URL, text, or clipboard content."""
    from bb.ingestion.manual import (
        ingest_url, ingest_text_from_editor, ingest_clipboard,
    )
    from bb.processing.block_generator import generate_block
    from bb.processing.validator import validate
    from bb.storage.block_store import generate_id, write_block
    from bb.storage.search_index import upsert_block
    from bb.storage.index_manifest import add_or_update, load_index

    if url and mode:
        raise click.UsageError("Provide either a URL or a flag (--text / --clipboard), not both.")
    if not url and not mode:
        raise click.UsageError("Provide a URL, --text, or --clipboard.")

    if url:
        console.print(f"Fetching [cyan]{url}[/cyan]…")
        inp = ingest_url(url, note=note)
    elif mode == "text":
        inp = ingest_text_from_editor()
    else:
        inp = ingest_clipboard()

    if note and mode:
        inp.note = note

    existing_ids: set[str] = {e["id"] for e in load_index()}
    id_source = inp.url or inp.raw_text[:500]
    block_id = generate_id(id_source, existing=existing_ids)

    console.print("Generating block…")

    block = generate_block(
        raw_text=inp.raw_text,
        url=inp.url,
        resolved_url=inp.resolved_url,
        source_type=inp.source_type,
        tweet_author=None,
        ingested_via=inp.source_type,
        block_id=block_id,
        note=inp.note,
    )
    validate(block)
    path = write_block(block)
    upsert_block(block)
    add_or_update(block)

    console.print(f"\n[green]Created[/green] [bold]{block.id}[/bold] → {path}")
    console.print(f"  Title:    {block.title}")
    console.print(f"  Category: {block.category}")
    console.print(f"  Tags:     {', '.join(block.tags)}")


# ---------------------------------------------------------------------------
# bb search
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("query")
def search(query: str):
    """FTS5 keyword search across all blocks."""
    from bb.storage.search_index import search as fts_search

    results = fts_search(query)
    if not results:
        console.print("No results found.")
        return

    table = Table(box=box.SIMPLE_HEAVY, show_header=True)
    table.add_column("ID", style="cyan", no_wrap=True, width=10)
    table.add_column("Title", no_wrap=True, width=30)
    table.add_column("Cat · Snippet", style="dim")

    for r in results:
        ctx = r.context or ""
        snippet = ctx[:60] + "…" if len(ctx) > 60 else ctx
        table.add_row(
            r.block_id,
            r.title,
            f"[yellow]{r.category}[/yellow]  {snippet}",
        )
    console.print(table)
    console.print(f"[dim]{len(results)} result(s)[/dim]")


# ---------------------------------------------------------------------------
# bb ask
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("query")
@click.option(
    "--deep", is_flag=True, default=False,
    help="Use LLM for reranking and a recommendation paragraph (slower).",
)
def ask(query: str, deep: bool):
    """FTS5 search with optional LLM reranking (--deep)."""
    from bb.query.retriever import ask as retriever_ask

    console.print(f"Searching for: [bold]{query}[/bold]\n")
    results, recommendation = retriever_ask(query, use_llm=deep)

    if not results:
        console.print("[yellow]No relevant blocks found.[/yellow]")
        return

    if recommendation:
        console.print(Panel(
            recommendation,
            title="[bold]Recommendation[/bold]",
            border_style="green",
            padding=(0, 1),
        ))
        console.print()

    table = Table(box=box.SIMPLE, show_header=True)
    table.add_column("#", style="dim", width=3)
    table.add_column("ID", style="cyan", no_wrap=True, width=10)
    table.add_column("Title")
    table.add_column("Why relevant", style="dim")

    for i, r in enumerate(results, 1):
        reason = r.reason or r.block.relevance_hint
        table.add_row(str(i), r.block.id, r.block.title, reason[:80])

    console.print(table)


# ---------------------------------------------------------------------------
# bb show
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("block_id")
def show(block_id: str):
    """Show full detail for a block (accepts ID prefix)."""
    from bb.storage.block_store import read_block, resolve_block_id

    try:
        full_id = resolve_block_id(block_id)
    except KeyError:
        console.print(f"[red]No block found matching {block_id!r}.[/red]")
        sys.exit(1)
    except ValueError as exc:
        matches = exc.args[0]
        console.print(f"[yellow]Ambiguous prefix {block_id!r} — matches:[/yellow]")
        for m in matches:
            console.print(f"  {m}")
        sys.exit(1)

    block = read_block(full_id)
    if not block:
        console.print(f"[red]Block {full_id!r} not found.[/red]")
        sys.exit(1)

    console.print(f"\n[bold cyan]{block.id}[/bold cyan]  [dim]{block.source_type}[/dim]")
    console.rule()
    console.print(f"[bold]Title:[/bold]          {block.title}")
    console.print(f"[bold]Category:[/bold]       {block.category}")
    console.print(f"[bold]Tags:[/bold]           {', '.join(block.tags)}")
    console.print(f"[bold]Created:[/bold]        {block.created}")
    console.print(f"[bold]Source:[/bold]         {block.source}")
    if block.source_resolved and block.source_resolved != block.source:
        console.print(f"[bold]Resolved URL:[/bold]   {block.source_resolved}")
    if block.tweet_author:
        console.print(f"[bold]Author:[/bold]         {block.tweet_author}")
    console.print(f"[bold]Ingested via:[/bold]   {block.ingested_via}")
    console.print(f"\n[bold]Context:[/bold]\n{block.context}")
    console.print(f"\n[bold]Relevance hint:[/bold]\n{block.relevance_hint}")
    console.print(f"\n[bold]Summary:[/bold]\n{block.summary}")


# ---------------------------------------------------------------------------
# bb related
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("block_id")
@click.option(
    "--deep", is_flag=True, default=False,
    help="Use LLM to filter related blocks (slower).",
)
def related(block_id: str, deep: bool):
    """Show blocks related to a given block (accepts ID prefix)."""
    from bb.query.recommender import find_related
    from bb.storage.block_store import resolve_block_id

    try:
        full_id = resolve_block_id(block_id)
    except KeyError:
        console.print(f"[red]No block found matching {block_id!r}.[/red]")
        sys.exit(1)
    except ValueError as exc:
        matches = exc.args[0]
        console.print(f"[yellow]Ambiguous prefix {block_id!r} — matches:[/yellow]")
        for m in matches:
            console.print(f"  {m}")
        sys.exit(1)

    console.print(f"Finding blocks related to [cyan]{full_id}[/cyan]…")
    blocks = find_related(full_id, use_llm=deep)
    if not blocks:
        console.print("No related blocks found.")
        return

    table = Table(box=box.SIMPLE, show_header=True)
    table.add_column("ID", style="cyan", no_wrap=True, width=10)
    table.add_column("Title")
    table.add_column("Category", style="yellow")
    table.add_column("Context", style="dim")

    for b in blocks:
        table.add_row(b.id, b.title, b.category, b.context[:70])
    console.print(table)


# ---------------------------------------------------------------------------
# bb list
# ---------------------------------------------------------------------------

@cli.command("list")
@click.option("--tag", default=None, help="Filter by tag.")
@click.option("--category", default=None, help="Filter by category.")
@click.option("--recent", default=None, metavar="Nd", help="Show blocks from last N days, e.g. 7d.")
def list_blocks(tag: str | None, category: str | None, recent: str | None):
    """List all blocks, optionally filtered."""
    from bb.storage.block_store import iter_all_blocks

    blocks = list(iter_all_blocks())

    if tag:
        blocks = [b for b in blocks if tag.lower() in [t.lower() for t in b.tags]]
    if category:
        blocks = [b for b in blocks if b.category.lower() == category.lower()]
    if recent:
        try:
            days = int(recent.rstrip("d"))
        except ValueError:
            raise click.BadParameter(f"Invalid format {recent!r} — use e.g. 7d")
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        blocks = [b for b in blocks if b.created >= cutoff]

    if not blocks:
        console.print("No blocks match.")
        return

    blocks.sort(key=lambda b: b.created, reverse=True)

    table = Table(box=box.SIMPLE, show_header=True)
    table.add_column("ID", style="cyan", no_wrap=True, width=10)
    table.add_column("Title")
    table.add_column("Cat", style="yellow", width=10)
    table.add_column("Tags", style="dim")
    table.add_column("Created", style="dim", width=12)

    for b in blocks:
        table.add_row(b.id, b.title[:55], b.category, ", ".join(b.tags[:4]), b.created)
    console.print(table)
    console.print(f"\n[dim]{len(blocks)} block(s)[/dim]")


# ---------------------------------------------------------------------------
# bb maintain
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--freshness", is_flag=True, help="Check URL liveness.")
def maintain(freshness: bool):
    """Re-cluster, deduplicate, and optionally check URL liveness."""
    if freshness:
        from bb.maintenance.freshness import run_freshness_check
        console.print("Checking URL liveness…\n")
        report = run_freshness_check(verbose=True)
        console.print(
            f"\nChecked [bold]{report.checked}[/bold] URLs — "
            f"[green]{report.alive} alive[/green], "
            f"[red]{report.dead} dead[/red], "
            f"[dim]{report.skipped} skipped[/dim]"
        )
        if report.newly_stale:
            console.print(f"\n[red]Newly marked stale ({len(report.newly_stale)}):[/red]")
            for bid in report.newly_stale:
                console.print(f"  {bid}")
        if report.already_stale:
            console.print(f"\n[dim]Already stale ({len(report.already_stale)}): {', '.join(report.already_stale)}[/dim]")
    else:
        from bb.maintenance.health_check import run_maintenance, apply_tag_suggestion
        console.print("Running maintenance (dedup → LLM analysis)…\n")
        report = run_maintenance()

        console.print(f"  Duplicates removed:  [cyan]{report.duplicates_removed}[/cyan]")

        if report.analysis:
            a = report.analysis

            if a.summary:
                console.print(f"\n[bold]Analysis[/bold]")
                console.print(Panel(a.summary, border_style="dim", padding=(0, 1)))

            if a.potential_dupes:
                console.print(f"\n[bold yellow]Potential duplicates[/bold yellow] ({len(a.potential_dupes)})")
                for pair in a.potential_dupes:
                    console.print(f"  {' / '.join(pair)}")

            if a.cross_references:
                console.print(f"\n[bold yellow]Cross-reference clusters[/bold yellow] ({len(a.cross_references)})")
                for xref in a.cross_references:
                    ids = ", ".join(xref.get("ids", []))
                    reason = xref.get("reason", "")
                    console.print(f"  [{ids}] — {reason}")

            if a.gaps:
                console.print(f"\n[bold yellow]Gaps identified[/bold yellow]")
                for gap in a.gaps:
                    console.print(f"  • {gap}")

            # Interactive tag suggestions
            if a.tag_suggestions:
                console.print(
                    f"\n[bold]Tag suggestions[/bold] — {len(a.tag_suggestions)} item(s). "
                    f"Review each and press [bold]y[/bold] to apply or [bold]n[/bold] to skip.\n"
                )
                total_added = 0
                for sug in a.tag_suggestions:
                    block_id = sug.get("id", "")
                    new_tags = sug.get("add_tags") or []
                    if not block_id or not new_tags:
                        continue
                    tag_str = ", ".join(f"[green]{t}[/green]" for t in new_tags)
                    console.print(f"  [cyan]{block_id}[/cyan] ← add {tag_str}")
                    if click.confirm("  Apply?", default=False):
                        added = apply_tag_suggestion(sug)
                        total_added += added
                        if added:
                            console.print(f"    [green]✓[/green] {added} tag(s) added.")
                        else:
                            console.print(f"    [dim]Already present, skipped.[/dim]")
                    else:
                        console.print(f"    [dim]Skipped.[/dim]")
                if total_added:
                    console.print(f"\n  Total tags added: [cyan]{total_added}[/cyan]")

        for err in report.errors:
            console.print(f"  [red]error:[/red] {err}")
        console.print("\n[green]Done.[/green]")


# ---------------------------------------------------------------------------
# bb digest
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--days", default=7, show_default=True, help="Look-back window in days.")
def digest(days: int):
    """Generate a 'what's new' digest of recent blocks grouped by category."""
    from collections import Counter
    from bb.maintenance.digest import generate_digest
    from bb.storage.block_store import iter_all_blocks

    cutoff = (date.today() - timedelta(days=days)).isoformat()
    recent = sorted(
        [b for b in iter_all_blocks() if b.created >= cutoff],
        key=lambda b: b.created,
        reverse=True,
    )

    if not recent:
        console.print(f"No blocks added in the last {days} days.")
        return

    # Grouped table by category
    by_cat: dict[str, list] = {}
    for b in recent:
        by_cat.setdefault(b.category, []).append(b)

    console.rule(f"[bold]Last {days} days[/bold] — {len(recent)} block(s)")
    for cat, blocks in sorted(by_cat.items()):
        table = Table(
            title=f"[yellow]{cat}[/yellow] ({len(blocks)})",
            box=box.SIMPLE,
            show_header=False,
            padding=(0, 1),
            title_justify="left",
        )
        table.add_column("ID", style="cyan", no_wrap=True, width=8)
        table.add_column("Title")
        table.add_column("Date", style="dim", width=12)
        for b in blocks:
            table.add_row(b.id, b.title, b.created)
        console.print(table)

    console.rule("[bold]Summary[/bold]")
    console.print(generate_digest(days))
    console.print()


# ---------------------------------------------------------------------------
# bb stats
# ---------------------------------------------------------------------------

@cli.command()
def stats():
    """Show counts, tag cloud, and timeline."""
    from collections import Counter
    from bb.storage.block_store import iter_all_blocks

    blocks = list(iter_all_blocks())
    if not blocks:
        console.print("No blocks yet.")
        return

    cat_counter: Counter = Counter(b.category for b in blocks)
    tag_counter: Counter = Counter(t for b in blocks for t in b.tags)
    month_counter: Counter = Counter(b.created[:7] for b in blocks)

    dates = [b.created for b in blocks if b.created]
    date_range = f"{min(dates)} → {max(dates)}" if dates else "—"

    console.print(f"\n[bold]Total blocks:[/bold] {len(blocks)}   [dim]Date range: {date_range}[/dim]\n")

    cat_table = Table(title="By Category", box=box.SIMPLE)
    cat_table.add_column("Category", style="yellow")
    cat_table.add_column("Count", style="cyan", justify="right")
    for cat, count in cat_counter.most_common():
        cat_table.add_row(cat, str(count))
    console.print(cat_table)

    tag_table = Table(title="Top 10 Tags", box=box.SIMPLE)
    tag_table.add_column("Tag", style="green")
    tag_table.add_column("Count", style="cyan", justify="right")
    for tag, count in tag_counter.most_common(10):
        tag_table.add_row(tag, str(count))
    console.print(tag_table)

    month_table = Table(title="Monthly Timeline", box=box.SIMPLE)
    month_table.add_column("Month")
    month_table.add_column("Blocks", style="cyan", justify="right")
    for month in sorted(month_counter.keys(), reverse=True):
        month_table.add_row(month, str(month_counter[month]))
    console.print(month_table)
