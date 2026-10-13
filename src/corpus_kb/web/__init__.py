"""Website (URL) ingestion: fetching, metadata extraction, section chunking.

The fetch layer (``fetch_page`` / ``crawl``) is transport-injected so tests
run on ``httpx.MockTransport``; ``metadata.extract_metadata`` produces the
JSON-safe 8-key web metadata dict; ``sections`` turns raw HTML into
heading-scoped, budget-packed section element groups for
``ingestion.strategies.web.WebStructureStrategy``.
"""

from __future__ import annotations

from .fetch import FetchedPage, WebFetchConfig, WebFetchError, crawl, fetch_page
from .metadata import METADATA_KEYS, extract_metadata
from .sections import (
    CHROME_CATEGORIES,
    CHROME_MIN_TEXT_CHARS,
    WebSection,
    group_sections,
    pack_elements,
    partition_html_proxies,
)
from .settings import config_from_mapping

__all__ = [
    "CHROME_CATEGORIES",
    "CHROME_MIN_TEXT_CHARS",
    "METADATA_KEYS",
    "FetchedPage",
    "WebFetchConfig",
    "WebFetchError",
    "WebSection",
    "config_from_mapping",
    "crawl",
    "extract_metadata",
    "fetch_page",
    "group_sections",
    "pack_elements",
    "partition_html_proxies",
]
