"""URL resolution and content scraping via httpx + trafilatura."""
from __future__ import annotations

from dataclasses import dataclass

import httpx
import trafilatura

from bb.config import HTTP_TIMEOUT


@dataclass
class ScrapedPage:
    url: str                  # final URL after redirects
    original_url: str
    title: str
    text: str                 # clean extracted text
    raw_html: str = ""


def resolve_and_scrape(url: str) -> ScrapedPage | None:
    """
    Fetch *url*, follow redirects, extract readable text with trafilatura.
    Returns None if the page can't be fetched or yields no extractable text.
    """
    try:
        response = httpx.get(
            url,
            follow_redirects=True,
            timeout=HTTP_TIMEOUT,
            headers={"User-Agent": "BookmarkBrain/0.1 (+https://github.com/asdelcampo/bookmark-brain)"},
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return None

    final_url = str(response.url)
    html = response.text

    extracted = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        no_fallback=False,
        favor_precision=True,
    )
    if not extracted:
        return None

    metadata = trafilatura.extract_metadata(html)
    title = (metadata.title if metadata else None) or _guess_title(url)

    return ScrapedPage(
        url=final_url,
        original_url=url,
        title=title,
        text=extracted,
        raw_html=html,
    )


def check_liveness(url: str) -> bool:
    """Return True if the URL responds with a 2xx status."""
    try:
        r = httpx.head(url, follow_redirects=True, timeout=HTTP_TIMEOUT)
        if r.status_code == 405:
            # HEAD not allowed — fall back to GET
            r = httpx.get(url, follow_redirects=True, timeout=HTTP_TIMEOUT)
        return r.is_success
    except httpx.HTTPError:
        return False


def _guess_title(url: str) -> str:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if path:
        return path.split("/")[-1].replace("-", " ").replace("_", " ").title()
    return parsed.netloc
