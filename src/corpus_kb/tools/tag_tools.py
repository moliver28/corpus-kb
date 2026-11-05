"""MCP tag/metadata tools: the ONE handler every surface routes through.

Issue #182: the tags/metadata reads previously had NO tool-layer function
— the HTTP routes called the ``TagHandler`` singleton directly and no MCP
tool existed. This module gives both reads their real MCP surface; the
parity catalogue runs every leg through these functions.

Phase-2 Wave 2 adds the four writes (``add_tag``, ``tag_document``,
``untag_document``, ``set_metadata``) so the drawer can edit through
manifest ops. Every write routes through the same ``TagHandler`` method
the HTTP route calls, so every surface returns the same envelope.

Same house pattern as ``tools/search_tools.py``: built over the CALLER'S
pool (the ``get_tag_handler`` singleton pins the first store it sees, and
these tools are handed a pool explicitly). Reads return exactly what the
GET routes return: ``{"status": "success", "result": [...]}``. Writes
return the handler's status envelope.
"""

from __future__ import annotations

from typing import Any

import asyncpg

from corpus_kb.tools._uuid_input import uuid_arg
from corpus_kb.tools.ingest_common import DEFAULT_TENANT_ID

__all__ = [
    "add_tag",
    "get_document_tags",
    "get_metadata",
    "set_metadata",
    "tag_document",
    "untag_document",
]


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


async def add_tag(
    pg_pool: asyncpg.Pool,
    name: str,
    color: str | None = None,
    description: str | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, Any]:
    """Create one tag in the tag vocabulary.

    Delegates to ``TagHandler.handle_add_tag`` — the same call
    POST /api/tags and the socket ``add_tag`` method make. The envelope
    is ``{"status": "success", "tag_id", "name"}`` on create and
    ``{"status": "already_exists", "name"}`` when the name is taken.
    """
    from corpus_kb.handlers.tag_handler import TagHandler

    handler = TagHandler(pg_pool)
    return await handler.handle_add_tag(uuid_arg(tenant_id, "tenant_id"), name, color, description)


async def tag_document(
    pg_pool: asyncpg.Pool,
    doc_id: str,
    tag: str,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, Any]:
    """Apply one tag to a document; a missing tag is created first.

    Delegates to ``TagHandler.handle_tag_document`` — the same call
    POST /api/documents/{doc_id}/tags and the socket ``tag_document``
    method make. The envelope is ``{"status", "doc_id", "tag"}``.
    """
    from corpus_kb.handlers.tag_handler import TagHandler

    handler = TagHandler(pg_pool)
    return await handler.handle_tag_document(
        uuid_arg(tenant_id, "tenant_id"), uuid_arg(doc_id, "doc_id"), tag
    )


async def untag_document(
    pg_pool: asyncpg.Pool,
    doc_id: str,
    tag: str,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, Any]:
    """Remove one tag from a document.

    Delegates to ``TagHandler.handle_untag_document`` — the same call
    POST /api/documents/{doc_id}/tags/remove and the socket
    ``untag_document`` method make. A tag the document does not carry is
    a typed ``not_found`` error, never a silent ok. The envelope is
    ``{"status", "doc_id", "tag"}``.
    """
    from corpus_kb.handlers.tag_handler import TagHandler

    handler = TagHandler(pg_pool)
    return await handler.handle_untag_document(
        uuid_arg(tenant_id, "tenant_id"), uuid_arg(doc_id, "doc_id"), tag
    )


async def set_metadata(
    pg_pool: asyncpg.Pool,
    key: str,
    value: str,
    doc_id: str | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, Any]:
    """Set one metadata key; the write is an upsert.

    Delegates to ``TagHandler.handle_set_metadata`` — the same call
    POST /api/metadata and the socket ``set_metadata`` method make. The
    envelope is ``{"status", "key", "value"}``.
    """
    from corpus_kb.handlers.tag_handler import TagHandler

    handler = TagHandler(pg_pool)
    return await handler.handle_set_metadata(
        uuid_arg(tenant_id, "tenant_id"),
        key,
        value,
        uuid_arg(doc_id, "doc_id") if doc_id else None,
    )
