"""MCP tag/metadata read tools: the ONE handler every surface routes through.

Issue #182: the tags/metadata reads previously had NO tool-layer function
— the HTTP routes called the ``TagHandler`` singleton directly and no MCP
tool existed. This module gives both reads their real MCP surface; the
parity catalogue runs every leg through these functions.

Same house pattern as ``tools/search_tools.py``: built over the CALLER'S
pool (the ``get_tag_handler`` singleton pins the first store it sees, and
these tools are handed a pool explicitly). Both return exactly what the
GET routes return: ``{"status": "success", "result": [...]}``.
"""

from __future__ import annotations

from typing import Any

import asyncpg

from corpus_kb.tools._uuid_input import uuid_arg
from corpus_kb.tools.ingest_common import DEFAULT_TENANT_ID

__all__ = ["get_document_tags", "get_metadata"]


async def get_document_tags(
    pg_pool: asyncpg.Pool,
    doc_id: str,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, Any]:
    """List one document's tags.

    Delegates to ``TagHandler.handle_get_document_tags`` — the same call
    GET /api/documents/{doc_id}/tags and the socket ``get_document_tags``
    method make — so every surface returns the same envelope:
    ``{"status", "result"}``. Rows carry name, color, and description.
    """
    from corpus_kb.handlers.tag_handler import TagHandler

    handler = TagHandler(pg_pool)
    rows = await handler.handle_get_document_tags(
        uuid_arg(tenant_id, "tenant_id"), uuid_arg(doc_id, "doc_id")
    )
    return {"status": "success", "result": rows}


async def get_metadata(
    pg_pool: asyncpg.Pool,
    tenant_id: str = DEFAULT_TENANT_ID,
    key: str | None = None,
    doc_id: str | None = None,
) -> dict[str, Any]:
    """Read metadata rows, optionally filtered by key or document.

    Delegates to ``TagHandler.handle_get_metadata`` — the same call
    GET /api/metadata and the socket ``get_metadata`` method make — so
    every surface returns the same envelope: ``{"status", "result"}``.
    Rows carry key, value, and doc_id.
    """
    from corpus_kb.handlers.tag_handler import TagHandler

    handler = TagHandler(pg_pool)
    rows = await handler.handle_get_metadata(
        uuid_arg(tenant_id, "tenant_id"),
        key,
        uuid_arg(doc_id, "doc_id") if doc_id else None,
    )
    return {"status": "success", "result": rows}
