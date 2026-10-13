"""Async website fetching: ``fetch_page`` + same-domain BFS ``crawl``.

Every network flow accepts an injected ``httpx.AsyncBaseTransport`` (tests use
``httpx.MockTransport`` — no test ever touches the network). Fetch rules,
in order: robots.txt (per-host cached, skippable), HTTP status, content-type
(text/html only), byte-size cap. Charset comes from the response headers via
httpx's decoder.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from lxml import html as lxml_html

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "corpus-kb/0.1 (+https://github.com/moliver28/corpus-kb)"


class WebFetchError(RuntimeError):
    """A fetch failed for a stated reason (robots-disallowed, content-type, ...)."""


@dataclass(frozen=True)
class WebFetchConfig:
    """Fetch/crawl knobs; the runtime config ``web:`` section is the contract."""

    user_agent: str = DEFAULT_USER_AGENT
    timeout_seconds: int = 30
    max_bytes: int = 5_242_880
    respect_robots: bool = True
    max_pages: int = 5
    max_depth: int = 2
    delay_seconds: float = 1.0


@dataclass(frozen=True)
class FetchedPage:
    """One successfully fetched HTML page (post-redirect)."""

    url: str
    final_url: str
    status_code: int
    html: str
    content_type: str
    fetched_at: str


def _make_client(
    config: WebFetchConfig, transport: httpx.AsyncBaseTransport | None
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=True,
        timeout=config.timeout_seconds,
        headers={"User-Agent": config.user_agent},
        transport=transport,
    )


def _robots_url(url: str) -> str:
    parts = urlparse(url)
    return f"{parts.scheme or 'https'}://{parts.netloc}/robots.txt"


async def _robots_allowed(
    client: httpx.AsyncClient,
    url: str,
    config: WebFetchConfig,
    cache: dict[str, bool],
) -> bool:
    """robots.txt gate, cached per host; an unreadable robots.txt never blocks."""
    host = urlparse(url).netloc.lower()
    if host in cache:
        return cache[host]
    allowed = True
    try:
        response = await client.get(_robots_url(url))
        if response.status_code == 200:
            parser = RobotFileParser()
            parser.parse(response.text.splitlines())
            allowed = parser.can_fetch(config.user_agent, url)
    except httpx.HTTPError as exc:
        logger.warning("robots.txt fetch failed for %s (allowing): %s", host, exc)
    cache[host] = allowed
    return allowed


async def _fetch_with_client(
    client: httpx.AsyncClient,
    url: str,
    config: WebFetchConfig,
    robots_cache: dict[str, bool],
) -> FetchedPage:
    if config.respect_robots and not await _robots_allowed(client, url, config, robots_cache):
        raise WebFetchError(f"robots-disallowed: {url}")
    response = await client.get(url)
    if response.status_code >= 400:
        raise WebFetchError(f"http-{response.status_code}: {url}")
    content_type = response.headers.get("content-type", "")
    if not content_type.lower().startswith("text/html"):
        raise WebFetchError(
            f"content-type: expected text/html, got '{content_type or 'none'}' ({url})"
        )
    if len(response.content) > config.max_bytes:
        raise WebFetchError(f"size-cap: {url} exceeds {config.max_bytes} bytes")
    return FetchedPage(
        url=url,
        final_url=str(response.url),
        status_code=response.status_code,
        html=response.text,
        content_type=content_type,
        fetched_at=datetime.now(UTC).isoformat(),
    )


async def fetch_page(
    url: str,
    config: WebFetchConfig,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FetchedPage:
    """Fetch ONE page (redirects followed, robots gated, HTML only)."""
    async with _make_client(config, transport) as client:
        return await _fetch_with_client(client, url, config, {})


def normalize_url(url: str) -> str:
    """Dedupe key: fragment stripped, scheme/host lowercased, query kept."""
    base = urldefrag(url).url
    parts = urlparse(base)
    query = f"?{parts.query}" if parts.query else ""
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path}{query}"


def _registrable_domain(host: str | None) -> str:
    """Last two DNS labels (a PSL-free approximation of the registrable domain)."""
    if not host:
        return ""
    labels = host.lower().rstrip(".").split(".")
    return host.lower() if len(labels) <= 2 else ".".join(labels[-2:])


def extract_links(page: FetchedPage) -> list[str]:
    """Absolute http(s) <a href> targets, <base href>-joined, parse-order."""
    try:
        doc = lxml_html.fromstring(page.html)
    except Exception:
        return []
    bases = doc.xpath("//base[@href]/@href")
    base = urljoin(page.final_url, bases[0].strip()) if bases else page.final_url
    links: list[str] = []
    for href in doc.xpath("//a/@href"):
        absolute = urljoin(base, href.strip())
        if urlparse(absolute).scheme in {"http", "https"}:
            links.append(absolute)
    return links


async def crawl(
    url: str,
    config: WebFetchConfig,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[FetchedPage]:
    """BFS same-registerable-domain crawl: max_pages + max_depth caps, URL dedupe.

    Per-page failures (robots, non-HTML, size, HTTP errors) are logged and
    skipped — a crawl never aborts on one bad page. ``delay_seconds`` sleeps
    between consecutive fetches (never before the first).
    """
    seed_domain = _registrable_domain(urlparse(url).hostname)
    pages: list[FetchedPage] = []
    seen: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(url, 0)])
    robots_cache: dict[str, bool] = {}
    async with _make_client(config, transport) as client:
        while queue and len(pages) < config.max_pages:
            current, depth = queue.popleft()
            normalized = normalize_url(current)
            if normalized in seen:
                continue
            seen.add(normalized)
            if pages:
                await asyncio.sleep(config.delay_seconds)
            try:
                page = await _fetch_with_client(client, current, config, robots_cache)
            except WebFetchError as exc:
                logger.warning("crawl skipped %s: %s", current, exc)
                continue
            pages.append(page)
            if depth < config.max_depth:
                for link in extract_links(page):
                    if _registrable_domain(urlparse(link).hostname) != seed_domain:
                        continue
                    if normalize_url(link) not in seen:
                        queue.append((link, depth + 1))
    return pages
