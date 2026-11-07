"""MCP chunk read tool: the ONE handler every surface routes through.

Phase-2 Wave 5: the Desk drawer's NLP panel scopes its analysis runs to
one document's chunks. ``chunks_list`` is that read — the GET route
``/api/documents/{doc_id}/chunks``, the socket ``chunks_list`` method,
the MCP tool, and the CLI command all answer through this function.

Same house pattern as ``tools/entity_read_tools.py:list_entities``: the
query handler is built over the CALLER'S storage seam (pool or
gateway), so a DuckDB gateway serves the read the same way a Postgres
pool does.
"""

from __future__ import annotations

import asyncpg

from corpus_kb.storage.gateway import SqlGateway
from corpus_kb.tools._uuid_input import uuid_arg
from corpus_kb.tools.ingest_common import DEFAULT_TENANT_ID, load_config_or_pass

__all__ = ["chunks_list"]


async def chunks_list(
    pg_pool: asyncpg.Pool | SqlGateway,
    doc_id: str,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, object]:
    """List one document's chunks, one window.

    Delegates to ``QueryHandler.handle_list_document_chunks`` — the same
    call GET /api/documents/{doc_id}/chunks makes. Rows are ordered
    ``(chunk_index, chunk_id)``; tombstoned chunks do not answer.
    Envelope: ``{"status", "result"}``.
    """
    from corpus_kb.domain.models import ListDocumentChunksQuery
    from corpus_kb.handlers.query_handler import QueryHandler

    query = ListDocumentChunksQuery(
        tenant_id=uuid_arg(tenant_id, "tenant_id"),
        doc_id=uuid_arg(doc_id, "doc_id"),
        limit=limit,
        offset=offset,
    )
    handler = QueryHandler(pg_pool, config=load_config_or_pass(config))
    results = await handler.handle_list_document_chunks(query)
    return {"status": "success", "result": [r.model_dump(mode="json") for r in results]}
