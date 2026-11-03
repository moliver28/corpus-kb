"""MCP search tools: the ONE search handler every surface routes through.

Wave 2 (zero-drift w2b): search previously had NO tool-layer function —
HTTP and socket both called ``QueryHandler.handle_search_with_meta`` plus
``envelope_to_payload`` directly, and the parity gate pinned that builder.
This module gives the operation its real MCP surface; the parity catalogue
now runs the tool leg through this function. Issue #182 adds the read
pair (``search_similar``/``search_context``): both delegate to the same
handler methods the HTTP routes call, so all surfaces share one envelope.

Same house pattern as ``tools/ingest_tools.py:list_documents``: built over
the CALLER'S pool (the ``get_query_handler`` singleton pins the first pool
it sees, and these tools are handed a pool explicitly).
"""

from __future__ import annotations

from typing import Any

import asyncpg

from corpus_kb.handlers.query_integrity import envelope_to_payload
from corpus_kb.tools._uuid_input import uuid_arg
from corpus_kb.tools.ingest_common import DEFAULT_TENANT_ID, load_config_or_pass

__all__ = ["search", "search_context", "search_similar"]


async def search(
    pg_pool: asyncpg.Pool,
    query: str,
    config: dict[str, Any] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    k: int | None = None,
    source_type: str | None = None,
    self_query: bool | None = None,
) -> dict[str, Any]:
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
        tenant_id=uuid_arg(tenant_id, "tenant_id"),
        query=query,
        k=k,
        source_type=source_type,
        self_query=self_query,
    )
    handler = QueryHandler(pg_pool, embedder, config=cfg)
    envelope = await handler.handle_search_with_meta(search_query)
    return envelope_to_payload(envelope)


async def search_similar(
    pg_pool: asyncpg.Pool,
    chunk_id: str,
    config: dict[str, Any] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    k: int = 10,
) -> dict[str, Any]:
    """Find chunks similar to one stored chunk by vector distance.

    Delegates to ``QueryHandler.handle_search_similar`` — the same call
    POST /api/search/similar and the socket ``search_similar`` method
    make — so every surface returns the same envelope:
    ``{"status", "result"}``. The seed chunk's STORED vector drives the
    search, so no embedder is built. ``k`` mirrors the catalogue row's
    default of 10 (the typed request model's default).
    """
    from corpus_kb.domain.models import SearchSimilarQuery
    from corpus_kb.handlers.query_handler import QueryHandler

    cfg = load_config_or_pass(config)
    similar_query = SearchSimilarQuery(
        tenant_id=uuid_arg(tenant_id, "tenant_id"),
        chunk_id=uuid_arg(chunk_id, "chunk_id"),
        k=k,
    )
    handler = QueryHandler(pg_pool, config=cfg)
    results = await handler.handle_search_similar(similar_query)
    return {"status": "success", "result": [r.model_dump(mode="json") for r in results]}


async def search_context(
    pg_pool: asyncpg.Pool,
    query: str,
    config: dict[str, Any] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    k: int = 5,
    context_chunks: int = 2,
) -> dict[str, Any]:
    """Search with the surrounding context chunks attached.

    Delegates to ``QueryHandler.handle_search_context`` — the same call
    POST /api/search/context and the socket ``search_context`` method
    make — so every surface returns the same envelope:
    ``{"status", "result"}``. The context search runs the hybrid search
    arms, so the config-driven embedder is built per call like ``search``
    does. ``k`` and ``context_chunks`` mirror the catalogue row's
    defaults of 5 and 2 (the typed request model's defaults).
    """
    from corpus_kb.domain.models import SearchContextQuery
    from corpus_kb.handlers.query_handler import QueryHandler
    from corpus_kb.rag import create_embedder

    cfg = load_config_or_pass(config)
    embedder = create_embedder(cfg, pg_pool)
    context_query = SearchContextQuery(
        tenant_id=uuid_arg(tenant_id, "tenant_id"),
        query=query,
        k=k,
        context_chunks=context_chunks,
    )
    handler = QueryHandler(pg_pool, embedder, config=cfg)
    results = await handler.handle_search_context(context_query)
    return {"status": "success", "result": [r.model_dump(mode="json") for r in results]}
