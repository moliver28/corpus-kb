"""MCP search tool: the ONE search handler every surface routes through.

Wave 2 (zero-drift w2b): search previously had NO tool-layer function —
HTTP and socket both called ``QueryHandler.handle_search_with_meta`` plus
``envelope_to_payload`` directly, and the parity gate pinned that builder.
This module gives the operation its real MCP surface; the parity catalogue
now runs the tool leg through this function.

Same house pattern as ``tools/ingest_tools.py:list_documents``: built over
the CALLER'S pool (the ``get_query_handler`` singleton pins the first pool
it sees, and this tool is handed a pool explicitly).
"""

from __future__ import annotations

from uuid import UUID

import asyncpg

from corpus_kb.handlers.query_integrity import envelope_to_payload
from corpus_kb.tools.ingest_common import DEFAULT_TENANT_ID, load_config_or_pass

__all__ = ["search"]


async def search(
    pg_pool: asyncpg.Pool,
    query: str,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    k: int | None = None,
    source_type: str | None = None,
    self_query: bool | None = None,
) -> dict[str, object]:
    """Hybrid vector + full-text search with the honest degraded envelope.

    Delegates to ``QueryHandler.handle_search_with_meta`` — the same call
    POST /api/search and the socket ``search`` method make — then serializes
    with ``envelope_to_payload``, so all three surfaces return the same
    envelope: ``{"status", "result", "degraded_excluded"}`` (plus ``remedy``
    only in the degraded_empty case). ``k=None`` (omitted) resolves to
    ``reranking.return_top_k`` inside the handler.

    The handler does NOT build an embedder itself (the server wiring owns
    that job and its comment warns what happens without one): a caller that
    skips it silently loses the vector arm. This entry builds the
    config-driven embedder per call so CLI and MCP callers get full hybrid
    search, not FTS-only results. Construction is cheap (no I/O).
    """
    from corpus_kb.domain.models import SearchQuery
    from corpus_kb.handlers.query_handler import QueryHandler
    from corpus_kb.rag import create_embedder

    cfg = load_config_or_pass(config)
    embedder = create_embedder(cfg, pg_pool)

    search_query = SearchQuery(
        tenant_id=UUID(tenant_id),
        query=query,
        k=k,
        source_type=source_type,
        self_query=self_query,
    )
    handler = QueryHandler(pg_pool, embedder, config=cfg)
    envelope = await handler.handle_search_with_meta(search_query)
    return envelope_to_payload(envelope)
