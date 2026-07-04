"""HTTP client for the production scraper.

Per scraper/recon/findings.md: no headless browser is needed at runtime.
Plain HTTP GETs work for every page type on stats.britishbaseball.org.uk as
long as a normal browser User-Agent header is sent (CloudFront blocks the
default httpx UA with a 403). Most pages embed their data as an Inertia.js
`data-page` JSON attribute in the HTML — extract_inertia_page() pulls that
out reliably (a naive regex is not reliable if the JSON payload happens to
contain a `">` sequence, so this uses a real HTML parser).
"""

import json
from html.parser import HTMLParser
from typing import Any

import httpx
from sqlalchemy.orm import Session
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from db.models import ScrapeLog
from scraper import cache
from scraper.rate_limiter import RateLimiter

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
}

_rate_limiter = RateLimiter()


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        # 403s from this site have been observed to be transient CloudFront
        # blips (confirmed by immediate retry succeeding during Milestone 7
        # historical scraping) rather than a persistent block — see
        # scraper/recon/findings.md. 404 is not retried: that means the
        # game/page genuinely doesn't exist.
        return exc.response.status_code in (403, 429, 500, 502, 503, 504)
    return False


@retry(
    # Kept short (2 attempts, small backoff) deliberately: if failures are a
    # sustained rate-limit window rather than a one-off blip, burning 30s+ of
    # backoff per request just delays hitting the same wall on the next
    # request too. scraper/pipeline.py's circuit breaker handles the
    # sustained case by pausing much longer at the orchestration level.
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=2, min=2, max=8),
    reraise=True,
)
def _get(url: str) -> httpx.Response:
    response = httpx.get(url, headers=BROWSER_HEADERS, timeout=30, follow_redirects=True)
    response.raise_for_status()
    return response


class _InertiaPageExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.data_page: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        if "data-page" in attr_dict:
            self.data_page = attr_dict["data-page"]


def extract_inertia_page(html: str) -> dict[str, Any] | None:
    parser = _InertiaPageExtractor()
    parser.feed(html)
    if parser.data_page is None:
        return None
    return json.loads(parser.data_page)


def fetch_html(
    url: str,
    entity_type: str,
    *,
    session: Session | None = None,
    source_id: str | None = None,
    force_refresh: bool = False,
    is_current_season: bool = True,
) -> str:
    """Fetch a URL's raw HTML, transparently caching the response.

    If `session` is given, records the fetch in scrape_log (source_id should
    identify what was fetched, e.g. a competition slug or game id).
    """
    ttl_hours = cache.ttl_for(is_current_season)
    cached = None if force_refresh else cache.get(entity_type, url, ttl_hours)
    if cached is not None:
        return cached

    _rate_limiter.wait()
    status = "ok"
    error_message = None
    cache_path = None
    try:
        response = _get(url)
        body = response.text
        cache_path = str(cache.put(entity_type, url, body))
    except httpx.HTTPError as exc:
        status = "error"
        error_message = str(exc)
        body = None

    if session is not None:
        session.add(
            ScrapeLog(
                entity_type=entity_type,
                source_id=source_id or url,
                cache_path=cache_path,
                status=status,
                error_message=error_message,
            )
        )
        session.commit()

    if body is None:
        raise RuntimeError(f"Failed to fetch {url}: {error_message}")
    return body


def fetch_inertia(
    url: str,
    entity_type: str,
    *,
    session: Session | None = None,
    source_id: str | None = None,
    force_refresh: bool = False,
    is_current_season: bool = True,
) -> dict[str, Any]:
    html = fetch_html(
        url,
        entity_type,
        session=session,
        source_id=source_id,
        force_refresh=force_refresh,
        is_current_season=is_current_season,
    )
    data = extract_inertia_page(html)
    if data is None:
        raise RuntimeError(f"No Inertia data-page found at {url}")
    return data
