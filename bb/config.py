"""Central configuration — paths, model settings, constants."""
from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# .env loader  (no external dependency — simple KEY=VALUE parsing)
# ---------------------------------------------------------------------------

def _load_dotenv() -> None:
    """Load PROJECT_ROOT/.env into os.environ (won't override existing vars)."""
    env_file = Path(__file__).parent.parent / ".env"
    if not env_file.exists():
        return
    with env_file.open() as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


_load_dotenv()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent

BLOCKS_DIR = Path(os.getenv("BB_BLOCKS_DIR", str(PROJECT_ROOT / "blocks"))).expanduser()
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "bb.db"
INDEX_PATH = BLOCKS_DIR / "_index.json"
PROCESSED_IDS_PATH = DATA_DIR / "processed_ids.json"

FT_BOOKMARKS_PATH = Path(
    os.getenv("BB_FT_PATH", str(Path.home() / ".ft-bookmarks" / "bookmarks.jsonl"))
).expanduser()

CATEGORY_DIRS: dict[str, Path] = {
    "tools": BLOCKS_DIR / "tools",
    "methods": BLOCKS_DIR / "methods",
    "articles": BLOCKS_DIR / "articles",
    "resources": BLOCKS_DIR / "resources",
    "other": BLOCKS_DIR / "other",
}

# ---------------------------------------------------------------------------
# LLM backend (llama-server OpenAI-compatible API)
# ---------------------------------------------------------------------------
LLM_BASE_URL = os.getenv("BB_LLM_URL", "http://localhost:8001/v1")
LLM_MODEL = os.getenv("BB_MODEL", "gemma-4-e2b")
LLM_TIMEOUT = 120  # seconds

# ---------------------------------------------------------------------------
# FTS5 search
# ---------------------------------------------------------------------------
FTS_TOP_K = 15   # candidates for FTS queries / LLM reranking input

# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------
STALE_DAYS = 180        # flag blocks whose URLs are unreachable after N days
HTTP_TIMEOUT = 10       # seconds for liveness checks

# ---------------------------------------------------------------------------
# Ensure runtime directories exist
# ---------------------------------------------------------------------------
def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for d in CATEGORY_DIRS.values():
        d.mkdir(parents=True, exist_ok=True)
