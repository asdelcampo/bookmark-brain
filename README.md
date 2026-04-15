# bookmark-brain

Local-first knowledge base that turns your X/Twitter bookmarks into a searchable, structured library — powered by llama.cpp and SQLite FTS5.

Bookmarks are ingested, summarised by a local LLM, and stored as plain Markdown files. Search and retrieval are instant with no LLM dependency. The LLM is only used when you explicitly ask for it (ingestion, `--deep` queries, maintenance analysis, digest).

---

## How it works

```
X bookmarks (via ft CLI)
        ↓
  bb sync / bb process
        ↓
  llama-server (gemma-4-e2b)
        ↓
  blocks/<category>/<id>.md   ←  plain Markdown + YAML frontmatter
        ↓
  SQLite FTS5 index  →  instant search / ask / related
```

Each block is a Markdown file with structured frontmatter:

```yaml
---
id: 72596
title: "Motion Core: Animated Svelte Component Library"
category: tools
tags: [svelte, animation, gsap, three.js, frontend]
context: "A component library for Svelte with GSAP and Three.js motion components."
relevance_hint: "When building interactive Svelte apps that need complex animations."
source: https://motion-core.dev/
created: 2026-04-11
---
2-4 sentence summary...
```

---

## Requirements

- Python 3.11+
- [llama-server](https://github.com/ggml-org/llama.cpp) running on port 8001 with a Gemma 4 E2B model
- [Field Theory CLI](https://github.com/teemblaze/field-theory) (`ft`) for X bookmark sync — optional if you use `bb add` manually

---

## Install

```bash
git clone https://github.com/asdelcampo/bookmark-brain
cd bookmark-brain
pip install -e .
```

Copy `.env.example` (or create `.env`) and point it at your llama-server:

```env
BB_LLM_URL=http://localhost:8001/v1
BB_MODEL=gemma-4-e2b
```

Start llama-server with your GGUF model:

```bash
llama-server --port 8001 -m /path/to/gemma-4-e2b-Q8_0.gguf
```

---

## Commands

### Ingestion

```bash
bb sync                     # ft sync + process new bookmarks
bb process                  # process queued bookmarks (skip ft sync)
bb process --limit 5        # process at most 5 at a time

bb add https://example.com  # manually ingest a URL
bb add --clipboard          # ingest from clipboard
bb add --text               # open $EDITOR for freeform text
bb add <url> --note "..."   # pass extra context to the LLM
```

### Search & retrieval (no LLM, instant)

```bash
bb search "svelte animation"          # FTS5 keyword search
bb ask "tools for CSS prototyping"    # FTS5 ranked by relevance_hint
bb related 72596                      # find similar blocks by tag overlap
bb show 72596                         # full block detail
bb show 725                           # prefix matching — works if unambiguous

bb ask "..." --deep                   # opt-in: LLM reranking + recommendation
bb related 72596 --deep               # opt-in: LLM-filtered related blocks
```

### Browse

```bash
bb list                         # all blocks, newest first
bb list --category tools
bb list --tag svelte
bb list --recent 7d             # added in the last 7 days
bb stats                        # counts, tag cloud, monthly timeline
```

### Maintenance

```bash
bb maintain                     # dedup by URL + LLM analysis
                                # → surfaces tag suggestions (interactive y/n),
                                #   potential duplicates, cross-reference clusters,
                                #   and knowledge gaps
                                # nothing is auto-deleted or auto-merged

bb maintain --freshness         # HEAD-check all URLs
                                # → marks dead links with stale: true in frontmatter
                                # → clears flag if URL recovers

bb digest                       # what's new this week, grouped by category
bb digest --days 30             # look back 30 days
```

---

## Block IDs

IDs are 5-character hex strings derived from the source URL (or content hash for manual entries). They're short enough to type but stable across renames.

```bash
bb show a7x2f     # exact
bb show a7x        # prefix — resolved if unambiguous
```

---

## Configuration

All settings are overridable via environment variables or `.env`:

| Variable | Default | Description |
|---|---|---|
| `BB_LLM_URL` | `http://localhost:8001/v1` | llama-server base URL |
| `BB_MODEL` | `gemma-4-e2b` | model name sent to the server |
| `BB_BLOCKS_DIR` | `./blocks` | where block files are stored |
| `BB_FT_PATH` | `~/.ft-bookmarks/bookmarks.jsonl` | Field Theory bookmarks file |

Override model at runtime:

```bash
bb --model gemma-4-e4b ask "..."
```

---

## Project layout

```
bb/
  cli.py              # Click commands
  config.py           # paths, LLM config
  ingestion/          # fieldtheory reader, URL scraper, manual input
  processing/         # LLM client (llama-server via openai SDK), block generator
  query/              # FTS5 retriever, related-block recommender
  storage/            # block file I/O, SQLite FTS5, _index.json manifest
  maintenance/        # health check, freshness, digest
blocks/
  tools/ articles/ methods/ resources/ other/
  _index.json         # lightweight block stubs (id, context, tags, category)
data/
  bb.db               # SQLite FTS5 index
```

---

## License

MIT
