"""Corpus stats tools: the ONE handler every surface routes through.

Phase-2 Wave 3: the two diagnostics reads previously had no tool layer —
the GET routes called the ``VersioningHandler`` singleton directly and no
MCP tool, CLI command, or socket method existed. This module gives both
ops their shared function; the catalogue rows name it, so the MCP tools
are generated and the parity gate runs every leg through these functions.

Same house pattern as ``tools/tag_tools.py``: built over the CALLER'S
pool (the ``get_versioning_handler`` singleton pins the first store it
sees, and these tools are handed a pool explicitly).

Envelopes: ``stats`` returns ``{"status", "stats"}`` — one row count per
key. ``document_stats`` returns ``{"status", "totals", "by_source_type"}``
— the named totals plus one row per source type. Both keep the handler's
deterministic key order, and the ``by_source_type`` rows sort by
``source_type`` (rule 9), so harness snapshots stay stable.
"""

from __future__ import annotations

from typing import Any

import asyncpg

from corpus_kb.tools._uuid_input import uuid_arg
from corpus_kb.tools.ingest_common import DEFAULT_TENANT_ID

__all__ = ["document_stats", "stats"]


async def stats(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, Any]:
    """Count the corpus rows (documents, chunks, vectors, graph, tags).

    Delegates to ``VersioningHandler.handle_get_stats`` — the one stats
    path — and wraps the counts in the shared envelope:
    ``{"status": "success", "stats": {...}}``. A missing event store
    answers ``total_events: 0``, never an error.
    """
    from corpus_kb.handlers.versioning_handler import VersioningHandler

    counts = await VersioningHandler(pg_pool).handle_get_stats(uuid_arg(tenant_id, "tenant_id"))
    return {"status": "success", "stats": counts}


async def document_stats(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, Any]:
    """Aggregate the document statistics: totals plus per-source-type rows.

    Delegates to ``VersioningHandler.handle_query_document_stats`` and
    wraps the answer in the shared envelope:
    ``{"status": "success", "totals": {...}, "by_source_type": [...]}``.
    The rows sort by ``source_type``, so the answer snapshots stably.
    """
    from corpus_kb.handlers.versioning_handler import VersioningHandler

    result = await VersioningHandler(pg_pool).handle_query_document_stats(
        uuid_arg(tenant_id, "tenant_id")
    )
    by_source = result.pop("by_source_type", [])
    return {"status": "success", "totals": result, "by_source_type": by_source}
