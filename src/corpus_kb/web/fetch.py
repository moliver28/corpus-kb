"""Async website fetching: ``fetch_page`` + same-site BFS ``crawl``.

Every network flow accepts an injected ``httpx.AsyncBaseTransport`` (tests use
``httpx.MockTransport`` — no test ever touches the network). Fetch rules, in
order PER REDIRECT HOP: private-address denylist (SSRF guard, escape hatch
``allow_private_addresses``), robots.txt (per-host cached, skippable), HTTP
status, content type (exactly ``text/html`` / ``application/xhtml+xml``), and
a byte-size cap enforced WHILE STREAMING the body — a multi-gigabyte response
can never buffer past the cap. Charset comes from the content-type header
(fallback utf-8, ``errors="replace"``).

Redirects are followed manually (a bounded hop loop over non-redirect
requests) so every intermediate hop passes the same denylist + robots gate a
direct request would. Every failure — transport errors (ReadTimeout,
ConnectError, TooManyRedirects, UnsupportedProtocol, ...) and invalid URLs
included — is converted to the structured :class:`WebFetchError`, and a crawl
log-and-skips bad pages: a crawl never aborts on one bad page.
"""

from __future__ import annotations

import asyncio
import codecs
import ipaddress
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

MAX_REDIRECT_HOPS = 10
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_HTML_MEDIA_TYPES = frozenset({"text/html", "application/xhtml+xml"})
# SSRF denylist: loopback, this-network, RFC1918 private, link-local (incl.
# the 169.254.169.254 cloud-metadata endpoint), IPv6 loopback/link-local/
# unique-local. Evaluated on the initial URL and after EVERY redirect hop.
_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "127.0.0.0/8",
        "0.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
        "::1/128",
        "fe80::/10",
        "fc00::/7",
    )
)


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
    allow_private_addresses: bool = False


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
    # follow_redirects=False on the wire: redirect hops are followed manually
    # (bounded loop) so each hop gets the denylist + robots gate.
    return httpx.AsyncClient(
        follow_redirects=False,
        timeout=config.timeout_seconds,
        headers={"User-Agent": config.user_agent},
        transport=transport,
    )


def _is_private_host(host: str | None) -> bool:
    """SSRF denylist test: literal private/loopback IPs and "localhost" names.

    Hostname DNS resolution is deliberately out of scope (it would add a
    resolver round-trip per hop and cannot be honored by injected test
    transports); the denylist covers literal IPs and the "localhost" name.
    """
    if not host:
        return False
    candidate = host.strip().strip("[]").lower().rstrip(".")
    if candidate == "localhost":
        return True
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return any(address in network for network in _PRIVATE_NETWORKS)


def _reject_private_address(url: str, config: WebFetchConfig) -> None:
    """Raise unless the URL's host passes the denylist (or the escape hatch)."""
    if config.allow_private_addresses:
        return
    host = urlparse(url).hostname
    if _is_private_host(host):
        raise WebFetchError(f"private-address-blocked: {host} ({url})")


def _robots_url(url: str) -> str:
    parts = urlparse(url)
    return f"{parts.scheme or 'https'}://{parts.netloc}/robots.txt"


async def _robots_allowed(
    client: httpx.AsyncClient,
    url: str,
    config: WebFetchConfig,
    cache: dict[str, bool],
) -> bool:
    """robots.txt gate, cached per host; an unreadable robots.txt never blocks.

    Fetched WITHOUT redirect following — a 3xx fails open exactly like an
    unreachable robots.txt instead of silently fetching a different origin.
    """
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


def _media_type(content_type: str) -> str:
    """Media type of a Content-Type header (``text/html; charset=x`` -> text/html)."""
    return content_type.split(";", 1)[0].strip().lower()


def _charset_of(content_type: str) -> str:
    """Usable charset parameter of a Content-Type header (utf-8 fallback)."""
    for part in content_type.split(";")[1:]:
        key, _, value = part.partition("=")
        if key.strip().lower() == "charset":
            candidate = value.strip().strip("\"'").strip()
            try:
                codecs.lookup(candidate)
            except LookupError:
                break
            return candidate
    return "utf-8"


async def _read_capped(response: httpx.Response, url: str, max_bytes: int) -> str:
    """Stream-decode the body, aborting the moment the byte cap is exceeded."""
    parts: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise WebFetchError(f"size-cap: {url} exceeds {max_bytes} bytes")
        parts.append(chunk)
    charset = _charset_of(response.headers.get("content-type", ""))
    return b"".join(parts).decode(charset, errors="replace")


async def _request_with_redirects(
    client: httpx.AsyncClient,
    url: str,
    config: WebFetchConfig,
    robots_cache: dict[str, bool],
) -> FetchedPage:
    """Bounded manual redirect loop; every hop passes denylist + robots + rules."""
    current = url
    response: httpx.Response | None = None
    for _hop in range(MAX_REDIRECT_HOPS + 1):
        _reject_private_address(current, config)
        if config.respect_robots and not await _robots_allowed(
            client, current, config, robots_cache
        ):
            raise WebFetchError(f"robots-disallowed: {current}")
        response = await client.send(client.build_request("GET", current), stream=True)
        if response.status_code not in _REDIRECT_STATUSES:
            break
        location = response.headers.get("location", "")
        await response.aclose()
        if not location:
            raise WebFetchError(f"redirect-without-location: {current}")
        next_url = urljoin(str(response.url), location)
        if urlparse(next_url).scheme not in {"http", "https"}:
            raise WebFetchError(f"unsupported-redirect-scheme: {next_url} (from {current})")
        current = next_url
    else:
        raise WebFetchError(f"too-many-redirects: more than {MAX_REDIRECT_HOPS} hops ({url})")
    assert response is not None
    try:
        if response.status_code >= 400:
            raise WebFetchError(f"http-{response.status_code}: {current}")
        content_type = response.headers.get("content-type", "")
        if _media_type(content_type) not in _HTML_MEDIA_TYPES:
            raise WebFetchError(
                f"content-type: expected text/html or application/xhtml+xml, "
                f"got '{content_type or 'none'}' ({current})"
            )
        html = await _read_capped(response, current, config.max_bytes)
        return FetchedPage(
            url=url,
            final_url=str(response.url),
            status_code=response.status_code,
            html=html,
            content_type=content_type,
            fetched_at=datetime.now(UTC).isoformat(),
        )
    finally:
        await response.aclose()


async def _fetch_with_client(
    client: httpx.AsyncClient,
    url: str,
    config: WebFetchConfig,
    robots_cache: dict[str, bool],
) -> FetchedPage:
    """One page through the full rule chain, with the structured error contract.

    Transport failures (ReadTimeout, ConnectError, TooManyRedirects,
    UnsupportedProtocol for e.g. ftp://, ...) and invalid URLs are converted
    to :class:`WebFetchError` — no raw httpx error ever escapes.
    """
    try:
        return await _request_with_redirects(client, url, config, robots_cache)
    except WebFetchError:
        raise
    except httpx.InvalidURL as exc:
        raise WebFetchError(f"invalid-url: {exc} ({url})") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise WebFetchError(f"network-error: {type(exc).__name__}: {exc} ({url})") from exc


async def fetch_page(
    url: str,
    config: WebFetchConfig,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FetchedPage:
    """Fetch ONE page (redirects gated per hop, robots, HTML only)."""
    async with _make_client(config, transport) as client:
        return await _fetch_with_client(client, url, config, {})


def normalize_url(url: str) -> str:
    """Dedupe key: fragment stripped, scheme/host lowercased, query kept."""
    base = urldefrag(url).url
    parts = urlparse(base)
    query = f"?{parts.query}" if parts.query else ""
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path}{query}"


def _same_site(left: str | None, right: str | None) -> bool:
    """Same-site scope: hostname equality modulo a leading "www.".

    ``www.example.com`` == ``example.com``; nothing else matches. The old
    two-label approximation grouped unrelated sites on multi-part public
    suffixes (bbc.co.uk and thetimes.co.uk both collapsed to "co.uk").
    """
    if not left or not right:
        return False
    left = left.lower().rstrip(".")
    right = right.lower().rstrip(".")
    return (left[4:] if left.startswith("www.") else left) == (
        right[4:] if right.startswith("www.") else right
    )


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
    errors: list[dict[str, str]] | None = None,
) -> list[FetchedPage]:
    """BFS same-site crawl: max_pages + max_depth caps, URL dedupe.

    Per-page failures (robots, private-address, non-HTML, size, HTTP,
    transport errors, invalid URLs) are logged and skipped — a crawl never
    aborts on one bad page. When ``errors`` is given, one ``{"url",
    "message"}`` entry per skipped page is appended so callers can surface
    honest per-page error dicts. ``delay_seconds`` sleeps between consecutive
    fetches (never before the first).
    """
    seed_host = urlparse(url).hostname
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
            except (WebFetchError, httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
                logger.warning("crawl skipped %s: %s", current, exc)
                if errors is not None:
                    errors.append({"url": current, "message": str(exc)})
                continue
            pages.append(page)
            if depth < config.max_depth:
                for link in extract_links(page):
                    if not _same_site(urlparse(link).hostname, seed_host):
                        continue
                    if normalize_url(link) not in seen:
                        queue.append((link, depth + 1))
    return pages
