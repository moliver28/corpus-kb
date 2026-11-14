"""DuckDB server startup — the full handler stack on the gateway (issue #330).

The Postgres ``startup`` in ``server_wiring.py`` is byte-identical in
shape; this module carries the ``database.backend: duckdb`` branch it
dispatches to. EVERY surface the Postgres path wires is wired here: the
eventsourcing app (SQLite sidecar), the command/query/graph/tag/
versioning/router/coding handler singletons, the projections, the
DuckDB RAG mirror, and the HTTP app + socket server. Every SQL-speaking
component takes the one ``SqlGateway`` (the handlers dispatch the engine
at runtime with ``is_gateway``); the event store is the eventsourcing
SQLite recorder file beside the analytics database.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from corpus_kb.dimensions import NOMIC_EMBEDDING_DIMENSIONS
from corpus_kb.projections.checkpoint import CheckpointManager
from corpus_kb.projections.dlq import DLQHandler
from corpus_kb.projections.documents_projection import DocumentsProjection
from corpus_kb.projections.embed_projection import EmbedChunksProjection
from corpus_kb.projections.event_reader import EventReader

if TYPE_CHECKING:
    from corpus_kb.rag.embedder import OllamaEmbedder, PgmlEmbedder

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Handlers:
    """The handler objects the DuckDB startup publishes."""

    embedder: OllamaEmbedder | PgmlEmbedder
    command_handler: object
    query_handler: object
    router_handler: object


@dataclass(frozen=True)
class _Projections:
    """The projection objects the DuckDB startup publishes."""

    embed_projection: EmbedChunksProjection
    docs_projection: DocumentsProjection
    event_reader: EventReader


async def startup_duckdb(cfg: dict[str, Any], conn_str: str) -> dict[str, Any]:
    """Start the full server stack on one DuckDB file behind the gateway."""
    from corpus_kb.domain.application import get_app
    from corpus_kb.storage.duckdb_rag import DuckDbRagBackend
    from corpus_kb.storage.gateway import DuckDbGateway

    gateway = await DuckDbGateway.connect(conn_str)
    # The event store is the SQLite recorder file BESIDE the .duckdb file —
    # never the analytics file itself (SQLite cannot open it). The shared
    # sidecar helper owns the name: get_app derives it through the same
    # path the migrate-data legs write to, so a verified migrate plus a
    # backend flip reopens the SAME store.
    app = get_app(conn_str, backend="duckdb")

    handlers = await _wire_handlers(cfg, gateway)
    projections = await _wire_projections(gateway, app, handlers.embedder)
    http = _wire_http(cfg)

    # RAG mirror (issue #330): the DuckDB arm of the RagBackend seam,
    # initialized the way LlamaIndexPostgresBackend is on Postgres.
    embedding = cast(dict[str, Any], cfg.get("embedding", {}) or {})
    dimensions = int(embedding.get("dimensions") or NOMIC_EMBEDDING_DIMENSIONS)
    rag_backend = DuckDbRagBackend(gateway, handlers.embedder, dimensions=dimensions)
    await rag_backend.initialize()

    logger.info("All components initialized")

    return {
        "pool": None,
        "gateway": gateway,
        "app": app,
        "command_handler": handlers.command_handler,
        "query_handler": handlers.query_handler,
        "embed_projection": projections.embed_projection,
        "docs_projection": projections.docs_projection,
        "event_reader": projections.event_reader,
        "http_app": http.http_app,
        "socket_server": http.socket_server,
        "config": cfg,
        "rag_backend": rag_backend,
        "router_handler": handlers.router_handler,
        "ui_mounted": http.ui_mounted,
        "notebooks_mounted": http.notebooks_mounted,
    }


async def _wire_handlers(cfg: dict[str, Any], gateway: Any) -> _Handlers:
    """The shared handler singletons, wired once for both engines.

    Mirrors ``server_wiring._wire_handlers``: the shared embedder (the
    gateway builds no pool-backed one — ``pg_pool_of`` answers None, so
    the Ollama embedder serves), the command/query stack, then the graph,
    tag, versioning, router, and coding handlers.
    """
    from corpus_kb.handlers.command_handler import get_command_handler
    from corpus_kb.handlers.idempotency import IdempotencyChecker, set_idempotency_checker
    from corpus_kb.handlers.query_handler import QueryHandler
    from corpus_kb.handlers.query_wiring import set_query_handler
    from corpus_kb.rag.embedder import create_embedder
    from corpus_kb.rag.judge import OllamaJudge
    from corpus_kb.rag.reranker import build_reranker
    from corpus_kb.rag.self_query import SelfQueryParser
    from corpus_kb.storage.connect import pg_pool_of

    embedder = create_embedder(cfg, pg_pool_of(gateway))
    reranker = build_reranker(cfg)
    self_query_parser = (
        SelfQueryParser(cfg)
        if (cfg.get("search", {}) or {}).get("self_query", {}).get("enabled", False)
        else None
    )
    judge = OllamaJudge(cfg) if (cfg.get("judge", {}) or {}).get("enabled", False) else None

    command_handler = get_command_handler(cfg, gateway=gateway)
    query_handler = QueryHandler(
        gateway,
        embedder,
        reranker=reranker,
        self_query_parser=self_query_parser,
        judge=judge,
        config=cfg,
    )
    set_query_handler(query_handler)
    set_idempotency_checker(IdempotencyChecker(gateway))

    router_handler = await _wire_aux_handlers(cfg, gateway, embedder, query_handler)
    return _Handlers(
        embedder=embedder,
        command_handler=command_handler,
        query_handler=query_handler,
        router_handler=router_handler,
    )


async def _wire_aux_handlers(cfg: dict[str, Any], gateway: Any, embedder: Any, query_handler: Any):
    """Graph, tag, versioning, router, and coding handlers (Postgres twin)."""
    from corpus_kb.handlers.coding_handler import CodingHandler, set_coding_handler
    from corpus_kb.handlers.graph_handler import GraphHandler, set_graph_handler
    from corpus_kb.handlers.router_handler import RouterHandler, set_router_handler
    from corpus_kb.handlers.tag_handler import TagHandler, set_tag_handler
    from corpus_kb.handlers.versioning_handler import VersioningHandler, set_versioning_handler
    from corpus_kb.storage.graph_factory import create_graph_store

    backend = str((cfg.get("database", {}) or {}).get("backend") or "duckdb")
    graph_store = await create_graph_store(cfg, gateway, backend)
    graph_handler = GraphHandler(graph_store, config=cfg)
    set_graph_handler(graph_handler)
    set_tag_handler(TagHandler(gateway))
    set_versioning_handler(VersioningHandler(gateway))

    router_handler = RouterHandler(
        query_handler, graph_handler, VersioningHandler(gateway), embedder, cfg
    )
    set_router_handler(router_handler)

    coding_cfg = cast(dict[str, Any], cfg.get("coding", {}) or {})
    set_coding_handler(
        CodingHandler(
            gateway,
            embedder,
            coder=str(coding_cfg.get("coder", "qwen3:8b")),
            model=str(coding_cfg.get("model", "qwen3:8b")),
        )
    )
    return router_handler


async def _wire_projections(gateway: Any, app: Any, embedder: Any) -> _Projections:
    """The projections, their reader, and the checkpoint/DLQ singletons."""
    from corpus_kb.domain.application import events_table_name
    from corpus_kb.projections.checkpoint import set_checkpoint_manager
    from corpus_kb.projections.dlq import set_dlq_handler
    from corpus_kb.projections.documents_projection import set_documents_projection
    from corpus_kb.projections.embed_projection import set_embed_projection

    checkpoint_mgr = CheckpointManager(gateway=gateway)
    dlq_handler = DLQHandler(gateway=gateway)
    set_checkpoint_manager(checkpoint_mgr)
    set_dlq_handler(dlq_handler)

    # Shared read path over the eventsourcing lib's events table (SQLite
    # recorder file on this backend — never a DuckDB table).
    event_reader = EventReader(None, app.mapper, events_table_name(app), recorder=app.recorder)

    embed_projection = EmbedChunksProjection(
        None, embedder, checkpoint_mgr, dlq_handler, gateway=gateway
    )
    set_embed_projection(embed_projection)

    docs_projection = DocumentsProjection(None, checkpoint_mgr, dlq_handler, gateway=gateway)
    set_documents_projection(docs_projection)
    return _Projections(
        embed_projection=embed_projection,
        docs_projection=docs_projection,
        event_reader=event_reader,
    )


def _wire_http(cfg: dict[str, Any]):
    """The HTTP app, the SPA and notebooks mounts, the socket."""
    from corpus_kb.api.http import create_http_app
    from corpus_kb.api.socket import get_socket_server
    from corpus_kb.api.socket_target import resolve_socket_target
    from corpus_kb.server_wiring import HttpWiring, wire_notebooks
    from corpus_kb.ui.spa import mount_spa

    http_app = create_http_app()
    ui_mounted = mount_spa(http_app, cfg)
    notebooks_mounted = wire_notebooks(http_app, cfg)
    # Issue #348: one socket endpoint per server (same rule as the
    # Postgres wiring) — derived from the port, never the shared default.
    socket_server = get_socket_server(resolve_socket_target(cfg))
    return HttpWiring(
        http_app=http_app,
        socket_server=socket_server,
        ui_mounted=ui_mounted,
        notebooks_mounted=notebooks_mounted,
    )
