"""MCP query/verify tools: the ONE handler every surface routes through.

Full-surface parity wave: ``query`` and ``verify_answer`` previously had
NO tool-layer function — HTTP and socket both called the handler singletons
directly and the parity catalogue pinned that builder. This module gives
both operations their real MCP surface; the parity catalogue now runs the
tool leg through these functions.

Pool seams (honest per handler): ``query`` resolves the wired
``RouterHandler`` singleton when a server wired one; standalone callers
(CLI, MCP stdio, tests) get the same stack built over the caller's pool —
graph store with the documented Age→Postgres fallback, embedder, and
versioning included. ``verify_answer`` builds its read handler over the
CALLER'S pool with the config-driven judge wired, mirroring
``tools/search_tools.py``. Both return exactly what POST /api/query and
POST /api/verify return: ``{"status": "success", "result": ...}``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import asyncpg

from corpus_kb.tools.ingest_common import DEFAULT_TENANT_ID, load_config_or_pass

if TYPE_CHECKING:
    from corpus_kb.handlers.router_handler import RouterHandler

__all__ = ["query", "verify_answer"]


async def _resolve_router_handler(cfg: dict[str, object], pool: asyncpg.Pool) -> RouterHandler:
    """Return the wired router, or build the full stack over this pool.

    Mirrors the server wiring's handler section (graph store with the
    documented Age→Postgres fallback, embedder-carrying query handler,
    versioning). One singleton per process, exactly like the server.
    """
    from corpus_kb.handlers.router_handler import get_router_handler, set_router_handler

    try:
        return get_router_handler()
    except RuntimeError:
        pass

    from corpus_kb.handlers.graph_handler import GraphHandler
    from corpus_kb.handlers.query_handler import QueryHandler
    from corpus_kb.handlers.router_handler import RouterHandler
    from corpus_kb.handlers.versioning_handler import VersioningHandler
    from corpus_kb.rag import create_embedder
    from corpus_kb.server_wiring import create_graph_store

    embedder = create_embedder(cfg, pool)
    query_handler = QueryHandler(pool, embedder, config=cfg)
    graph_store = await create_graph_store(cfg, pool)
    router = RouterHandler(
        query_handler,
        GraphHandler(graph_store),
        VersioningHandler(pool),
        embedder,
        cfg,
    )
    set_router_handler(router)
    return router


async def query(
    query: str,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    k: int | None = None,
    pg_pool: asyncpg.Pool | None = None,
) -> dict[str, object]:
    """Route one question to the vector, SQL, or graph backend.

    Delegates to the ``RouterHandler`` — the same handler POST /api/query
    and the socket ``query`` method resolve — and serializes the routed
    result, so every surface returns the same envelope:
    ``{"status", "result"}``. ``k=None`` (omitted) resolves the backend
    default inside the handler.

    When no server has wired the router singleton, the stack is built over
    ``pg_pool`` (injected by the MCP wrapper, passed by the CLI). With
    neither, a pool is opened from the config's connection string.
    """
    from corpus_kb.domain.models import RoutedQuery

    cfg = load_config_or_pass(config)
    own_pool: asyncpg.Pool | None = None
    pool = pg_pool
    if pool is None:
        import asyncpg as _asyncpg

        dsn = str((cfg.get("database", {}) or {}).get("connection_string", ""))
        pool = own_pool = await _asyncpg.create_pool(dsn)
    try:
        router = await _resolve_router_handler(cfg, pool)
        routed = RoutedQuery(tenant_id=UUID(tenant_id), query=query, k=k)
        result = await router.handle_routed_query(routed)
        return {"status": "success", "result": result.model_dump(mode="json")}
    finally:
        if own_pool is not None:
            await own_pool.close()


async def verify_answer(
    pg_pool: asyncpg.Pool,
    answer: str = "",
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    chunk_ids: list[str] | None = None,
) -> dict[str, object]:
    """Check each claim of an answer against the cited chunks.

    Delegates to ``QueryHandler.handle_verify_answer`` — the same call
    POST /api/verify and the socket ``verify_answer`` method make — so
    every surface returns the same envelope: ``{"status", "result"}``.
    Empty ``chunk_ids`` means nothing cited; the verifier abstains.
    The config-driven judge is wired here (the server wiring's exact
    rule): without it the handler abstains on every call, which silently
    downgrades the operation to "always abstain".
    ``tenant_id`` scopes the chunk lookup the same way the HTTP route does.
    """
    from corpus_kb.domain.models import VerifyAnswerQuery
    from corpus_kb.handlers.query_handler import QueryHandler

    cfg = load_config_or_pass(config)
    judge = None
    if (cfg.get("judge", {}) or {}).get("enabled", False):
        from corpus_kb.rag.judge import OllamaJudge

        judge = OllamaJudge(cfg)

    verified = VerifyAnswerQuery(
        answer=answer,
        chunk_ids=[UUID(chunk) for chunk in (chunk_ids or [])],
    )
    handler = QueryHandler(pg_pool, judge=judge, config=cfg)
    result = await handler.handle_verify_answer(verified)
    return {"status": "success", "result": result.model_dump(mode="json")}
