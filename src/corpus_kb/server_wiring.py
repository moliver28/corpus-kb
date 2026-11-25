"""Server wiring — initializes all components and starts all protocols.

Two transports:

  * stdio: the MCP tool surface for editor agents
    (`corpus_kb.mcp_server.run_stdio_server` — the registry-generated
    server; this module only routes to it).
  * http/sse: full stack, started by :func:`startup` + :func:`run_all`:
    1. Load config
    2. Initialize asyncpg pool (Postgres)
    3. Initialize CorpusApplication (eventsourcing)
    4. Initialize handlers (command, query, idempotency)
    5. Start projections (EmbedChunksProjection, DocumentsProjection)
    6. Start HTTP server (uvicorn + Starlette)
    7. Start socket server (JSON-RPC)

All HTTP-mode services run on the same asyncio event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import asyncpg

from corpus_kb.contracts.model import DEFAULT_TENANT_ID
from corpus_kb.errors import ConfigError, CorpusError, DatabaseUnavailableError
from corpus_kb.storage.graph_factory import create_graph_store

if TYPE_CHECKING:
    from fastapi import FastAPI

    from corpus_kb.api.socket import JSONRPCServer
    from corpus_kb.domain.application import CorpusApplication
    from corpus_kb.handlers.command_handler import CommandHandler
    from corpus_kb.handlers.query_handler import QueryHandler
    from corpus_kb.handlers.router_handler import RouterHandler
    from corpus_kb.projections.documents_projection import DocumentsProjection
    from corpus_kb.projections.embed_projection import EmbedChunksProjection
    from corpus_kb.projections.event_reader import EventReader
    from corpus_kb.rag.embedder import OllamaEmbedder, PgmlEmbedder
    from corpus_kb.storage.gateway import DuckDbGateway
    from corpus_kb.storage.llamaindex_backend import LlamaIndexPostgresBackend

logger = logging.getLogger(__name__)


def wire_notebooks(http_app: object, cfg: dict[str, Any]) -> bool:
    """Mount the notebooks console when the config gate is on. NEVER raises.

    UX program Wave 6: the gate mirrors ``mount_spa`` — behind
    ``notebooks.enabled``, the lazy ``[notebook]`` extra, one honest log
    line on absence, and the server continues either way.
    """
    from corpus_kb.notebooks.console import mount_notebooks
    from corpus_kb.notebooks.settings import load_notebooks_settings

    settings = load_notebooks_settings(cfg)
    if not settings.enabled:
        return False
    return mount_notebooks(http_app, settings.directory)


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
        raise DatabaseUnavailableError(
            f"Failed to connect to Postgres at {connection_string}: {exc}",
            remediation="Run corpus-kb setup. Check the DSN and credentials.",
        ) from exc
    logger.info("asyncpg pool created")
    return pool


def _setup_tracing(cfg: dict[str, Any]) -> None:
    """Step 0 (UX program Wave 4): tracing setup must never break startup.

    Disabled (the default) is a no-op; a missing [otel] extra or any
    setup failure logs ONE honest line and the server continues with
    tracing off.
    """
    try:
        from corpus_kb.utils.tracing import setup_tracing

        setup_tracing(cfg)
    except Exception as exc:
        remedy = getattr(exc, "remediation", "")
        logger.warning("Tracing not enabled: %s. Remedy: %s", exc, remedy)


async def _wire_query_stack(
    cfg: dict[str, Any], pool: asyncpg.Pool | DuckDbGateway
) -> tuple[OllamaEmbedder | PgmlEmbedder, QueryHandler, CommandHandler]:
    """Step 3: the shared embedder and the query-side singletons.

    The shared embedder: the query side needs it for vector search, the
    embed projection needs it to backfill vectors. Without it here,
    handle_search silently skips vector search and degrades to full-text
    only. The pool-driven factories take ``pg_pool_of(pool)`` — None on
    the gateway, which builds the Ollama embedder and no pool-backed
    reranker (issue #330: one wiring shape, both engines).
    """
    from corpus_kb.handlers.command_handler import get_command_handler
    from corpus_kb.handlers.idempotency import IdempotencyChecker, set_idempotency_checker
    from corpus_kb.handlers.query_handler import QueryHandler, set_query_handler
    from corpus_kb.rag import create_embedder
    from corpus_kb.rag.judge import OllamaJudge
    from corpus_kb.rag.reranker import create_reranker
    from corpus_kb.rag.self_query import SelfQueryParser
    from corpus_kb.storage.connect import pg_pool_of

    pg_pool = pg_pool_of(pool)
    embedder = create_embedder(cfg, pg_pool)
    reranker = create_reranker(cfg, pg_pool)
    self_query_parser = (
        SelfQueryParser(cfg)
        if (cfg.get("retrieval", {}) or {}).get("self_query", {}).get("enabled", False)
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
    return embedder, query_handler, command_handler


async def _wire_aux_handlers(
    cfg: dict[str, Any],
    pool: asyncpg.Pool | DuckDbGateway,
    backend: str,
    embedder: OllamaEmbedder | PgmlEmbedder,
    query_handler: QueryHandler,
) -> RouterHandler:
    """Steps 3b-3d: graph, tag, versioning, router, and coding handlers.

    The router wraps the query, graph, and versioning handlers (adaptive
    routing across vector/SQL/graph backends); the coding handler backs
    the deductive coding pipeline behind /api/coding/*.
    """
    from corpus_kb.handlers.coding_handler import CodingHandler, set_coding_handler
    from corpus_kb.handlers.graph_handler import GraphHandler, set_graph_handler
    from corpus_kb.handlers.router_handler import RouterHandler, set_router_handler
    from corpus_kb.handlers.tag_handler import TagHandler, set_tag_handler
    from corpus_kb.handlers.versioning_handler import VersioningHandler, set_versioning_handler

    graph_store = await create_graph_store(cfg, pool, backend)
    graph_handler = GraphHandler(graph_store, config=cfg)
    set_graph_handler(graph_handler)
    set_tag_handler(TagHandler(pool))
    set_versioning_handler(VersioningHandler(pool))

    router_handler = RouterHandler(
        query_handler, graph_handler, VersioningHandler(pool), embedder, cfg
    )
    set_router_handler(router_handler)

    coding_cfg = cast(dict[str, Any], cfg.get("coding", {}) or {})
    set_coding_handler(
        CodingHandler(
            pool,
            embedder,
            coder=str(coding_cfg.get("coder", "qwen3:8b")),
            model=str(coding_cfg.get("model", "qwen3:8b")),
        )
    )
    return router_handler


@dataclass(frozen=True)
class HandlerWiring:
    """The handler objects ``startup`` publishes in the services dict."""

    app: CorpusApplication
    command_handler: CommandHandler
    query_handler: QueryHandler
    router_handler: RouterHandler
    embedder: OllamaEmbedder | PgmlEmbedder


async def _wire_handlers(
    cfg: dict[str, Any], pool: asyncpg.Pool | DuckDbGateway, conn_str: str, backend: str
) -> HandlerWiring:
    """Steps 2-3d: the eventsourcing app and every handler singleton."""
    from corpus_kb.domain.application import get_app

    app = get_app(conn_str, backend=backend)
    embedder, query_handler, command_handler = await _wire_query_stack(cfg, pool)
    router_handler = await _wire_aux_handlers(cfg, pool, backend, embedder, query_handler)
    return HandlerWiring(
        app=app,
        command_handler=command_handler,
        query_handler=query_handler,
        router_handler=router_handler,
        embedder=embedder,
    )


@dataclass(frozen=True)
class ProjectionWiring:
    """The projection objects ``startup`` publishes in the services dict."""

    embed_projection: EmbedChunksProjection
    docs_projection: DocumentsProjection
    event_reader: EventReader
    rag_backend: LlamaIndexPostgresBackend | None


async def _wire_projections(
    cfg: dict[str, Any],
    pool: asyncpg.Pool,
    app: CorpusApplication,
    *,
    embedder: OllamaEmbedder | PgmlEmbedder,
) -> ProjectionWiring:
    """Step 4 + 4b: the projections, their reader, and the RAG backend.

    The shared read path runs over the eventsourcing lib's events table
    (topic/state + notification_id bigserial), decoding via the app
    Mapper. The table is lib-owned and named after the application class.
    """
    from corpus_kb.domain.application import events_table_name
    from corpus_kb.projections.checkpoint import CheckpointManager, set_checkpoint_manager
    from corpus_kb.projections.dlq import DLQHandler, set_dlq_handler
    from corpus_kb.projections.documents_projection import (
        DocumentsProjection,
        set_documents_projection,
    )
    from corpus_kb.projections.embed_projection import EmbedChunksProjection, set_embed_projection
    from corpus_kb.projections.event_reader import EventReader
    from corpus_kb.storage.llamaindex_backend import create_initialized_rag_backend

    checkpoint_mgr = CheckpointManager(pool)
    dlq_handler = DLQHandler(pool)
    set_checkpoint_manager(checkpoint_mgr)
    set_dlq_handler(dlq_handler)

    event_reader = EventReader(pool, app.mapper, events_table_name(app))

    embed_projection = EmbedChunksProjection(pool, embedder, checkpoint_mgr, dlq_handler)
    set_embed_projection(embed_projection)

    docs_projection = DocumentsProjection(pool, checkpoint_mgr, dlq_handler)
    set_documents_projection(docs_projection)

    # LlamaIndex RAG backend: None without the [llamaindex] extra (one honest log line).
    rag_backend = await create_initialized_rag_backend(cfg)
    return ProjectionWiring(
        embed_projection=embed_projection,
        docs_projection=docs_projection,
        event_reader=event_reader,
        rag_backend=rag_backend,
    )


@dataclass(frozen=True)
class HttpWiring:
    """The HTTP surface ``startup`` publishes in the services dict."""

    http_app: FastAPI
    socket_server: JSONRPCServer
    ui_mounted: bool
    notebooks_mounted: bool


def _wire_http(cfg: dict[str, Any]) -> HttpWiring:
    """Steps 5-6: the HTTP app, the SPA and notebooks mounts, the socket.

    A missing UI build logs ONE honest line inside mount_spa and the
    server continues — a broken UI must not kill the API. The notebooks
    console (UX program Wave 6) mirrors that contract.
    """
    from corpus_kb.api.http import create_http_app
    from corpus_kb.api.socket import get_socket_server
    from corpus_kb.api.socket_target import resolve_socket_target
    from corpus_kb.ui.spa import mount_spa

    http_app = create_http_app()
    ui_mounted = mount_spa(http_app, cfg)
    notebooks_mounted = wire_notebooks(http_app, cfg)
    # Issue #348: one socket endpoint per server — derived from the port
    # (or server.socket_path / CORPUS_KB_SOCKET_PATH), never the shared default.
    socket_server = get_socket_server(resolve_socket_target(cfg))
    return HttpWiring(
        http_app=http_app,
        socket_server=socket_server,
        ui_mounted=ui_mounted,
        notebooks_mounted=notebooks_mounted,
    )


async def startup(
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
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

    # 0. Observability: tracing (never breaks startup).
    _setup_tracing(cfg)

    # 1. Database backend: read ONCE, then wire per ecosystem (program §3.4).
    #    A duckdb selection is refused here — typed — not deep in a pool-only handler.
    db_cfg = cfg.get("database", {})
    conn_str = db_cfg.get("connection_string", "") or os.environ.get("CORPUS_KB_DATABASE_URL", "")
    if not conn_str:
        raise ConfigError(
            "No database connection string. Set CORPUS_KB_DATABASE_URL or "
            "configure database.connection_string in config.yaml."
        )
    backend = str(db_cfg.get("backend") or "postgres")
    if backend == "duckdb":
        # Research ecosystem on the gateway (phase E sp-research-port).
        from corpus_kb.server_wiring_duckdb import startup_duckdb

        return await startup_duckdb(cfg, conn_str)
    if backend != "postgres":
        raise ConfigError(
            "The server backend must be postgres or duckdb.",
            remediation="Set database.backend to postgres or duckdb.",
        )

    # Boot guard (W5-a): a store NEWER than this code refuses, typed,
    # before anything wires; older or fresh stores proceed unchanged.
    from corpus_kb._setup.version_guard import assert_store_supported

    await assert_store_supported(conn_str, backend)

    pool = await initialize_postgres_pool(conn_str)

    handlers = await _wire_handlers(cfg, pool, conn_str, backend)
    projections = await _wire_projections(cfg, pool, handlers.app, embedder=handlers.embedder)
    http = _wire_http(cfg)

    logger.info("All components initialized")

    return {
        "pool": pool,
        "app": handlers.app,
        "command_handler": handlers.command_handler,
        "query_handler": handlers.query_handler,
        "embed_projection": projections.embed_projection,
        "docs_projection": projections.docs_projection,
        "event_reader": projections.event_reader,
        "http_app": http.http_app,
        "socket_server": http.socket_server,
        "config": cfg,
        "rag_backend": projections.rag_backend,
        "router_handler": handlers.router_handler,
        "ui_mounted": http.ui_mounted,
        "notebooks_mounted": http.notebooks_mounted,
    }


async def run_all(services: dict[str, Any]) -> None:
    """Run HTTP + socket servers concurrently. MCP runs separately via FastMCP.

    Projections run as background tasks within the same event loop.
    """
    import uvicorn

    http_app = services["http_app"]
    socket_server = services["socket_server"]
    embed_projection = services["embed_projection"]
    docs_projection = services["docs_projection"]
    event_reader = services["event_reader"]
    config = services["config"]

    server_cfg = config.get("server", {})
    host = server_cfg.get("host", "localhost")
    port = int(server_cfg.get("port", 8010))

    # Start socket server
    await socket_server.start()

    # Start projection background tasks (ALL registered projections run — the
    # pre-spike wiring started only embed_projection, leaving DocumentsProjection
    # constructed but never running).
    from uuid import UUID

    default_tenant = UUID(DEFAULT_TENANT_ID)
    projection_task = asyncio.create_task(embed_projection.run(default_tenant, event_reader))
    docs_projection_task = asyncio.create_task(docs_projection.run(default_tenant, event_reader))

    # Browser auto-open (UX program Wave 3): fires ONLY when the [ui]
    # extra actually mounted and CORPUS_KB_UI_OPEN is not "0".
    # Lazy import beside the SPA mount: the auto-open guard lives with the
    # UI surface it depends on (corpus_kb.ui.browser_open).
    from corpus_kb.ui.browser_open import schedule_browser_open

    browser_task = schedule_browser_open(services, str(host), port)

    # Queue autorun (textbook program Wave 3): ONE background drain task
    # when queue.autorun is true; the default false starts nothing. The
    # worker runs on the pool or the gateway (issue #330 queue twins); a
    # typed refusal logs one line while the server continues.
    from corpus_kb.queue.autorun import start_queue_autorun

    queue_autorun = start_queue_autorun(services["pool"] or services.get("gateway"), config)

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
        if queue_autorun is not None:
            await queue_autorun.stop()
        if browser_task is not None:
            browser_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await browser_task
        await socket_server.stop()
        projection_task.cancel()
        docs_projection_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await projection_task
            await docs_projection_task
        logger.info("All servers stopped")


async def shutdown(services: dict[str, Any]) -> None:
    """Graceful shutdown: close pools, stop projections."""
    pool = services["pool"]
    if isinstance(pool, asyncpg.Pool):
        await pool.close()
    gateway = services.get("gateway")
    if gateway is not None:
        await gateway.close()
    # Observability (UX program Wave 4): flush pending spans on the way out.
    # Best-effort and safe when tracing was never enabled.
    from corpus_kb.utils.tracing import shutdown_tracing

    shutdown_tracing()
    logger.info("asyncpg pool closed")


def main(argv: list[str] | None = None) -> None:
    """Entry point: initialize and run the selected transport.

    Supports --transport (stdio|http|sse) and --port CLI args.
    Default: http. stdio serves the registry-generated MCP tools over
    stdin/stdout for editor agents. HTTP/SSE starts the Starlette HTTP
    server + JSON-RPC socket + projections.

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
    parser.add_argument(
        "--host",
        default=None,
        help="HTTP bind host (default: server.host config, then localhost)",
    )
    ui_group = parser.add_mutually_exclusive_group()
    ui_group.add_argument(
        "--ui",
        dest="ui",
        action="store_true",
        default=None,
        help="Serve the browser UI (overrides the ui.enabled config key)",
    )
    ui_group.add_argument(
        "--no-ui",
        dest="ui",
        action="store_false",
        help="Disable the browser UI (overrides the ui.enabled config key)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    if args.transport == "stdio":
        # The MCP tool surface for editor agents: the registry-generated
        # server (mcp_server.py) owns pool, build, and the stdio loop.
        from corpus_kb.mcp_server import run_stdio_server

        sys.exit(run_stdio_server())
    else:
        # HTTP/SSE mode: start all protocols
        logger.info("Starting in %s mode on port %d", args.transport, args.port)

        async def _run() -> None:
            cfg: dict[str, Any] | None = None
            if args.ui is not None or args.host is not None or "CORPUS_KB_PORT" not in os.environ:
                # The --ui/--no-ui flag overrides the ui.enabled config key;
                # the --port flag fills in for an unset CORPUS_KB_PORT (the
                # documented priority is env var, then CLI flag, then YAML).
                # --host works the same way: the flag fills server.host so
                # containers and NAS hosts can bind 0.0.0.0. Load once here
                # so startup and run_all see the overrides.
                from corpus_kb.config import load_config

                cfg = load_config()
                server_block = cast(dict[str, Any], cfg.setdefault("server", {}))
                if args.ui is not None:
                    ui_block = cast(dict[str, Any], cfg.setdefault("ui", {}))
                    ui_block["enabled"] = bool(args.ui)
                if "CORPUS_KB_PORT" not in os.environ:
                    server_block["port"] = args.port
                if args.host is not None:
                    server_block["host"] = args.host
            services = await startup(cfg)
            try:
                await run_all(services)
            finally:
                await shutdown(services)

        try:
            asyncio.run(_run())
        except KeyboardInterrupt:
            logger.info("Server interrupted by user")
        except CorpusError as exc:
            # Issue #348: a refused socket takeover (and every other typed
            # failure) exits with one clear line and the taxonomy's exit
            # code — never a traceback and never a silent takeover.
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(exc.cli_exit)


if __name__ == "__main__":
    main()
