"""MCP ``ingest_url`` tool: fetch (or crawl) a website -> run_pipeline.

One handler shared by every surface (MCP tool layer, HTTP
``POST /api/ingest/url``, the socket ``ingest_url`` method, and CLI
``corpus-kb ingest-url``). Lives in its own module (not ``ingest_tools.py``):
that file sits at the 250-line soft limit and this surface set is versioned
separately in the MCP contract snapshot (the ``ingest_surface_tools``
precedent). ``tools.ingest_tools`` re-exports ``ingest_url`` so the
``corpus_kb.tools.ingest_tools`` import path stays the one-stop tool layer.
"""

from __future__ import annotations

import asyncpg
import httpx

from ..ingestion.registry import strategy_names
from ..web.fetch import WebFetchError, fetch_page
from ..web.fetch import crawl as crawl_pages
from ..web.settings import config_from_mapping as web_config_from_mapping
from .ingest_common import load_config_or_pass, run_pipeline

__all__ = ["ingest_url"]


def _validate_strategy_override(source_type: str) -> str | None:
    """Return an error message for an unknown strategy override, else None."""
    if source_type in strategy_names():
        return None
    return (
        f"Unknown source-type strategy '{source_type}'. "
        f"Valid strategies: {', '.join(sorted(strategy_names()))}"
    )


async def ingest_url(
    url: str,
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = "00000000-0000-0000-0000-000000000001",
    crawl: bool = False,
    source_type: str | None = None,
    validate: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, object]:
    """Fetch (or crawl) a website and run the ingest pipeline per page.

    Each page's raw HTML flows through ``run_pipeline`` with document
    source_type "web"; the web_structure strategy wins detection for that
    hint and extracts the page metadata from the HTML itself, so this stays
    a thin fetch+pipeline loop. Per-page failures are caught into the
    aggregate — one bad page never aborts the batch, and crawl-skipped pages
    (raw httpx errors are converted to the structured WebFetchError contract
    inside the fetch layer) come back as per-page error dicts.

    The fetch config comes from the ``web:`` config section (code defaults
    only when keys are absent). ``transport`` injects an httpx transport
    (tests use httpx.MockTransport; production leaves it None).
    ``validate`` is the optional validation gate handed to every page's
    pipeline run (off | advisory | enforce | calibrate); None falls back
    to the active profile's ``validate`` value, then ``off``.

    Returns ``{"status": "success", "pages": [...], "total_documents": N}``
    where ``pages`` holds one run_pipeline result dict (or a structured
    error dict) per fetched page.
    """
    config = load_config_or_pass(config)
    if source_type is not None:
        error = _validate_strategy_override(source_type)
        if error is not None:
            return {"status": "error", "message": error}
    fetch_config = web_config_from_mapping(config.get("web"))
    fetch_errors: list[dict[str, str]] = []
    try:
        if crawl:
            pages = await crawl_pages(url, fetch_config, transport=transport, errors=fetch_errors)
        else:
            pages = [await fetch_page(url, fetch_config, transport=transport)]
    except WebFetchError as exc:
        return {"status": "error", "message": str(exc)}
    if not pages:
        return {"status": "error", "message": f"no pages fetched from {url}"}

    results: list[dict[str, object]] = []
    for page in pages:
        try:
            results.append(
                await run_pipeline(
                    page.html,
                    "web",
                    page.final_url,
                    config,
                    pg_pool,
                    tenant_id,
                    is_file=False,
                    strategy_name=source_type,
                    validate_mode=validate,
                )
            )
        except Exception as exc:
            results.append(
                {
                    "status": "error",
                    "url": page.final_url,
                    "message": f"{type(exc).__name__}: {exc}",
                }
            )
    # Pages the crawl log-and-skipped (transport errors, robots, size cap, ...)
    # surface as per-page error dicts — the skip is never silent in the result.
    results.extend({"status": "error", **entry} for entry in fetch_errors)
    total_documents = sum(1 for result in results if result.get("status") == "success")
    return {"status": "success", "pages": results, "total_documents": total_documents}
