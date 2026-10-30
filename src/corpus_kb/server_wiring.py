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
import webbrowser
from typing import Any, cast

import asyncpg

from corpus_kb.errors import ConfigError, DatabaseUnavailableError
from corpus_kb.storage.graph_factory import create_graph_store

logger = logging.getLogger(__name__)

# Browser auto-open (UI SPA program): set to "0" to keep the server
# from opening a browser tab (tests, headless hosts). Anything else —
# including unset — follows the /app/ SPA mount outcome.
UI_OPEN_ENV_VAR = "CORPUS_KB_UI_OPEN"

UI_OPEN_DELAY_SECONDS = 1.5


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


def browser_open_enabled(environ: dict[str, str] | None = None) -> bool:
    """The auto-open guard: ``CORPUS_KB_UI_OPEN=0`` keeps the tab closed."""
    env = os.environ if environ is None else environ
    return env.get(UI_OPEN_ENV_VAR) != "0"


async def open_browser_when_up(host: str, port: int, delay: float = UI_OPEN_DELAY_SECONDS) -> None:
    """Open the browser once the server has had a moment to bind."""
    await asyncio.sleep(delay)
    webbrowser.open(f"http://{host}:{port}")


def schedule_browser_open(
    services: dict[str, Any],
    host: str,
    port: int,
) -> asyncio.Task[None] | None:
    """Schedule the delayed auto-open, or return None when it must not fire.

    Fires ONLY when the ``/app/`` SPA actually mounted (``ui_mounted``
    in the services dict) AND the env guard allows it. The browser opens
    the server root, which 307s to ``/app/``.
    """
    if not services.get("ui_mounted", False):
        return None
    if not browser_open_enabled():
        return None
    return asyncio.create_task(open_browser_when_up(host, port))


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

    # 0. Observability (UX program Wave 4): tracing setup must never break
    #    startup. Disabled (the default) is a no-op; a missing [otel] extra
    #    or any setup failure logs ONE honest line and the server continues
    #    with tracing off.
    try:
        from corpus_kb.utils.tracing import setup_tracing

        setup_tracing(cfg)
    except Exception as exc:
        remedy = getattr(exc, "remediation", "")
        logger.warning("Tracing not enabled: %s. Remedy: %s", exc, remedy)

    # 1. Database backend: read ONCE, then wire per ecosystem (program §3.4).
    #    The asyncpg pool exists only on the Postgres path and the event
    #    store is keyed on (backend, DSN). The remaining handlers are still
    #    Postgres-only (phases D/E own those ports), so a duckdb selection
    #    is refused here — typed, with the fix — instead of failing deep
    #    inside a pool-only handler.
    db_cfg = cfg.get("database", {})
    conn_str = db_cfg.get("connection_string", "") or os.environ.get("CORPUS_KB_DATABASE_URL", "")
    if not conn_str:
        raise ConfigError(
            "No database connection string. Set CORPUS_KB_DATABASE_URL or "
            "configure database.connection_string in config.yaml."
        )
    backend = str(db_cfg.get("backend") or "postgres")
    if backend == "duckdb":
        # Research ecosystem on the gateway (phase E sp-research-port):
        # SQLite event store + gateway-built projections; handlers whose
        # port has not landed stay unwired and fail typed per route.
        from corpus_kb.server_wiring_duckdb import startup_duckdb

        return await startup_duckdb(cfg, conn_str)
    if backend != "postgres":
        raise ConfigError(
            "The server backend must be postgres or duckdb.",
            remediation="Set database.backend to postgres or duckdb.",
        )

    pool = await initialize_postgres_pool(conn_str)

    # 2. Eventsourcing application
    from corpus_kb.domain.application import events_table_name, get_app

    app = get_app(conn_str, backend=backend)

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

    graph_store = await create_graph_store(cfg, pool, backend)
    graph_handler = GraphHandler(graph_store, config=cfg)
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

    coding_cfg = cast(dict[str, Any], cfg.get("coding", {}) or {})
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
    from corpus_kb.projections.event_reader import EventReader

    checkpoint_mgr = CheckpointManager(pool)
    dlq_handler = DLQHandler(pool)
    set_checkpoint_manager(checkpoint_mgr)
    set_dlq_handler(dlq_handler)

    # Shared read path over the eventsourcing lib's events table
    # (topic/state + notification_id bigserial), decoding via the app Mapper.
    # The table is lib-owned and named after the application class.
    event_reader = EventReader(pool, app.mapper, events_table_name(app))

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

    # 5b. Browser UI (UI SPA program): the lifecycle SPA mounts at /app.
    # A missing build logs ONE honest line inside mount_spa; the server
    # continues — a broken UI must not kill the API.
    from corpus_kb.ui.spa import mount_spa

    ui_mounted = mount_spa(http_app, cfg)

    # 5c. marimo notebooks console (UX program Wave 6): the lazy [notebook]
    #     extra behind notebooks.enabled, mounted at /notebooks on the SAME
    #     HTTP app (no new daemon, no new port). Absence logs ONE honest
    #     line and continues — the 5b contract, mirrored.
    notebooks_mounted = wire_notebooks(http_app, cfg)

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
        "event_reader": event_reader,
        "http_app": http_app,
        "socket_server": socket_server,
        "config": cfg,
        "rag_backend": rag_backend,
        "router_handler": router_handler,
        "ui_mounted": ui_mounted,
        "notebooks_mounted": notebooks_mounted,
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

    default_tenant = UUID("00000000-0000-0000-0000-000000000001")
    projection_task = asyncio.create_task(embed_projection.run(default_tenant, event_reader))
    docs_projection_task = asyncio.create_task(docs_projection.run(default_tenant, event_reader))

    # Browser auto-open (UX program Wave 3): fires ONLY when the [ui]
    # extra actually mounted and CORPUS_KB_UI_OPEN is not "0".
    browser_task = schedule_browser_open(services, str(host), port)

    # Queue autorun (textbook program Wave 3): ONE background drain task
    # when queue.autorun is true; the default false starts nothing. The
    # worker's typed refusal covers DuckDB, and a refusal logs one line
    # while the server continues.
    from corpus_kb.queue.autorun import start_queue_autorun

    queue_autorun = start_queue_autorun(services["pool"], config)

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
            if args.ui is not None or "CORPUS_KB_PORT" not in os.environ:
                # The --ui/--no-ui flag overrides the ui.enabled config key;
                # the --port flag fills in for an unset CORPUS_KB_PORT (the
                # documented priority is env var, then CLI flag, then YAML).
                # Load once here so startup and run_all see the overrides.
                from corpus_kb.config import load_config

                cfg = load_config()
                server_block = cast(dict[str, Any], cfg.setdefault("server", {}))
                if args.ui is not None:
                    ui_block = cast(dict[str, Any], cfg.setdefault("ui", {}))
                    ui_block["enabled"] = bool(args.ui)
                if "CORPUS_KB_PORT" not in os.environ:
                    server_block["port"] = args.port
            services = await startup(cfg)
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
