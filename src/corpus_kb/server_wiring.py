"""Server wiring — initializes all components and starts all protocols.

On startup:
  1. Load config
  2. Initialize asyncpg pool (Postgres)
  3. Initialize CorpusApplication (eventsourcing)
  4. Initialize handlers (command, query, idempotency)
  5. Start projections (EmbedChunksProjection, DocumentsProjection)
  6. Start HTTP server (uvicorn + Starlette)
  7. Start socket server (JSON-RPC)
  8. Start MCP server (FastMCP)

All run on the same asyncio event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from typing import TYPE_CHECKING, cast

import asyncpg

if TYPE_CHECKING:
    from corpus_kb.storage.graph_store import GraphStore

logger = logging.getLogger(__name__)


async def create_graph_store(
    cfg: dict[str, object],
    pool: asyncpg.Pool,
) -> GraphStore:
    """Instantiate the configured GraphStore backend.

    backend="age" tries AgeGraphStore first and falls back to
    PostgresGraphStore when Apache AGE is unavailable. backend="postgres"
    selects PostgresGraphStore directly.
    """
    from corpus_kb.storage import AgeGraphStore, AgeUnavailableError, PostgresGraphStore

    graph_cfg = cfg.get("graph", {})
    backend = graph_cfg.get("backend", "age")

    if backend == "age":
        try:
            store: GraphStore = AgeGraphStore(pool)
            await store.search_entities("")  # probe AGE availability
            return store
        except AgeUnavailableError as exc:
            logger.warning(
                "Apache AGE graph backend unavailable: %s. Falling back to PostgresGraphStore.",
                exc,
            )
            return PostgresGraphStore(pool)
    if backend == "postgres":
        return PostgresGraphStore(pool)
    raise ValueError(f"Unknown graph backend: {backend!r}. Use 'age' or 'postgres'.")


async def initialize_postgres_pool(
    connection_string: str,
) -> asyncpg.Pool:
    """Create and return an asyncpg connection pool."""
    try:
        pool = await asyncpg.create_pool(
            connection_string,
            min_size=5,
            max_size=20,
            command_timeout=60.0,
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to connect to Postgres at {connection_string}: {exc}") from exc
    logger.info("asyncpg pool created")
    return pool


async def startup(
    config: dict[str, object] | None = None,
) -> dict[str, object]:
    """Initialize all components. Returns a dict of initialized services.

    Call this once at server startup. The returned dict contains:
      - pool: asyncpg connection pool
      - app: CorpusApplication (eventsourcing)
      - command_handler: CommandHandler
      - query_handler: QueryHandler
      - embed_projection: EmbedChunksProjection
      - docs_projection: DocumentsProjection
      - http_app: Starlette app
      - socket_server: JSONRPCServer
    """
    from corpus_kb.config import load_config

    cfg = config or load_config()

    # 1. Postgres pool
    db_cfg = cfg.get("database", {})
    conn_str = db_cfg.get("connection_string", "") or os.environ.get("CORPUS_KB_DATABASE_URL", "")
    if not conn_str:
        raise RuntimeError(
            "No database connection string. Set CORPUS_KB_DATABASE_URL or "
            "configure database.connection_string in config.yaml."
        )

    pool = await initialize_postgres_pool(conn_str)

    # 2. Eventsourcing application
    from corpus_kb.domain.application import get_app

    app = get_app(conn_str)

    # 3. Handlers
    from corpus_kb.handlers.command_handler import get_command_handler
    from corpus_kb.handlers.idempotency import IdempotencyChecker, set_idempotency_checker
    from corpus_kb.handlers.query_handler import QueryHandler, set_query_handler

    # Shared embedder: the query side needs it for vector search, the embed
    # projection needs it to backfill vectors. Without it here, handle_search
    # silently skips vector search and degrades to full-text only.
    from corpus_kb.rag import create_embedder
    from corpus_kb.rag.judge import OllamaJudge
    from corpus_kb.rag.reranker import create_reranker
    from corpus_kb.rag.self_query import SelfQueryParser

    embedder = create_embedder(cfg, pool)

    reranker = create_reranker(cfg, pool)
    self_query_parser = (
        SelfQueryParser(cfg)
        if (cfg.get("search", {}) or {}).get("self_query", {}).get("enabled", False)
        else None
    )
    judge = OllamaJudge(cfg) if (cfg.get("judge", {}) or {}).get("enabled", False) else None

    command_handler = get_command_handler(cfg, pool)
    query_handler = QueryHandler(
        pool,
        embedder,
        reranker=reranker,
        self_query_parser=self_query_parser,
        judge=judge,
        config=cfg,
    )
    set_query_handler(query_handler)
    set_idempotency_checker(IdempotencyChecker(pool))

    # 3b. Graph, Tag, Versioning handlers
    from corpus_kb.handlers.graph_handler import GraphHandler, set_graph_handler
    from corpus_kb.handlers.tag_handler import TagHandler, set_tag_handler
    from corpus_kb.handlers.versioning_handler import VersioningHandler, set_versioning_handler

    graph_store = await create_graph_store(cfg, pool)
    graph_handler = GraphHandler(graph_store)
    set_graph_handler(graph_handler)
    set_tag_handler(TagHandler(pool))
    set_versioning_handler(VersioningHandler(pool))

    # 3c. Router handler (adaptive routing across vector/SQL/graph backends)
    from corpus_kb.handlers.router_handler import RouterHandler, set_router_handler

    router_handler = RouterHandler(
        query_handler, graph_handler, VersioningHandler(pool), embedder, cfg
    )
    set_router_handler(router_handler)

    # 3d. Coding handler (deductive coding pipeline behind /api/coding/*)
    from corpus_kb.handlers.coding_handler import CodingHandler, set_coding_handler

    coding_cfg = cast(dict[str, object], cfg.get("coding", {}) or {})
    set_coding_handler(
        CodingHandler(
            pool,
            embedder,
            coder=str(coding_cfg.get("coder", "qwen3:8b")),
            model=str(coding_cfg.get("model", "qwen3:8b")),
        )
    )

    # 4. Projections
    from corpus_kb.projections.checkpoint import CheckpointManager, set_checkpoint_manager
    from corpus_kb.projections.dlq import DLQHandler, set_dlq_handler
    from corpus_kb.projections.documents_projection import (
        DocumentsProjection,
        set_documents_projection,
    )
    from corpus_kb.projections.embed_projection import EmbedChunksProjection, set_embed_projection

    checkpoint_mgr = CheckpointManager(pool)
    dlq_handler = DLQHandler(pool)
    set_checkpoint_manager(checkpoint_mgr)
    set_dlq_handler(dlq_handler)

    # Embedder for projection (provider selected by embedding.provider)
    from corpus_kb.rag import create_embedder

    embedder = create_embedder(cfg, pool)
    embed_projection = EmbedChunksProjection(pool, embedder, checkpoint_mgr, dlq_handler)
    set_embed_projection(embed_projection)

    docs_projection = DocumentsProjection(pool, checkpoint_mgr, dlq_handler)
    set_documents_projection(docs_projection)

    # 4b. LlamaIndex RAG backend (additive, Ollama-only)
    from corpus_kb.storage.llamaindex_backend import LlamaIndexPostgresBackend

    rag_backend = LlamaIndexPostgresBackend(cfg)
    await rag_backend.initialize()

    # 5. HTTP app
    from corpus_kb.api.http import create_http_app

    http_app = create_http_app()

    # 6. Socket server
    from corpus_kb.api.socket import get_socket_server

    socket_server = get_socket_server()

    logger.info("All components initialized")

    return {
        "pool": pool,
        "app": app,
        "command_handler": command_handler,
        "query_handler": query_handler,
        "embed_projection": embed_projection,
        "docs_projection": docs_projection,
        "http_app": http_app,
        "socket_server": socket_server,
        "config": cfg,
        "rag_backend": rag_backend,
        "router_handler": router_handler,
    }


async def run_all(services: dict[str, object]) -> None:
    """Run HTTP + socket servers concurrently. MCP runs separately via FastMCP.

    Projections run as background tasks within the same event loop.
    """
    import uvicorn

    http_app = services["http_app"]
    socket_server = services["socket_server"]
    embed_projection = services["embed_projection"]
    config = services["config"]

    server_cfg = config.get("server", {})
    host = server_cfg.get("host", "localhost")
    port = int(server_cfg.get("port", 8010))

    # Start socket server
    await socket_server.start()

    # Start projection background tasks
    from uuid import UUID

    default_tenant = UUID("00000000-0000-0000-0000-000000000001")
    projection_task = asyncio.create_task(embed_projection.run(default_tenant))

    # Start HTTP server via uvicorn
    config_obj = uvicorn.Config(
        http_app,
        host=host,
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config_obj)

    try:
        logger.info("Starting HTTP server on %s:%d", host, port)
        await server.serve()
    finally:
        socket_server.stop()
        projection_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await projection_task
        logger.info("All servers stopped")


async def shutdown(services: dict[str, object]) -> None:
    """Graceful shutdown: close pools, stop projections."""
    pool = services["pool"]
    if isinstance(pool, asyncpg.Pool):
        await pool.close()
    logger.info("asyncpg pool closed")


def main(argv: list[str] | None = None) -> None:
    """Entry point: initialize and run all services.

    Supports --transport (stdio|http|sse) and --port CLI args.
    Default: stdio (MCP over stdin/stdout for editor agents).
    HTTP/SSE: starts Starlette HTTP server + JSON-RPC socket + projections.

    Args:
        argv: Optional argument list. When omitted, ``sys.argv`` is used.
    """
    import argparse

    parser = argparse.ArgumentParser(description="Corpus-KB Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http", "sse"],
        default="http",
        help="Transport mode (default: http)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8010,
        help="HTTP server port (default: 8010)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    if args.transport == "stdio":
        # MCP stdio mode: run FastMCP server only (no HTTP/socket)
        logger.info("Starting in stdio mode (MCP only)")

        # TODO: wire FastMCP server here
        # For now, just run the HTTP server
        async def _run() -> None:
            services = await startup()
            try:
                await run_all(services)
            finally:
                await shutdown(services)

        asyncio.run(_run())
    else:
        # HTTP/SSE mode: start all protocols
        logger.info("Starting in %s mode on port %d", args.transport, args.port)

        async def _run() -> None:
            services = await startup()
            try:
                await run_all(services)
            finally:
                await shutdown(services)

        try:
            asyncio.run(_run())
        except KeyboardInterrupt:
            logger.info("Server interrupted by user")


if __name__ == "__main__":
    main()
