"""DuckDB server startup — the research ecosystem on the gateway (phase E).

The Postgres ``startup`` in ``server_wiring.py`` is byte-identical; this
module carries the ``database.backend: duckdb`` branch it dispatches to.
It constructs the gateway-based research dependencies with the
projections-port pattern: the event store is the eventsourcing SQLite
recorder file (``recorder=`` on the reader), and every SQL-speaking
research component takes the one ``SqlGateway`` (``gateway=``).

Handlers that still speak raw asyncpg (command/query/graph surfaces) are
NOT wired here — the startup returns honestly without them, and their
port is the remaining phase-E work recorded in the program log.
"""

from __future__ import annotations

import logging
from typing import Any

from corpus_kb.projections.checkpoint import CheckpointManager
from corpus_kb.projections.dlq import DLQHandler
from corpus_kb.projections.documents_projection import DocumentsProjection
from corpus_kb.projections.embed_projection import EmbedChunksProjection
from corpus_kb.projections.event_reader import EventReader

logger = logging.getLogger(__name__)


async def startup_duckdb(cfg: dict[str, Any], conn_str: str) -> dict[str, Any]:
    """Start the research ecosystem on one DuckDB file behind the gateway."""
    from corpus_kb.domain.application import events_table_name, get_app
    from corpus_kb.rag.embedder import create_embedder
    from corpus_kb.storage.gateway import DuckDbGateway

    gateway = await DuckDbGateway.connect(conn_str)
    # The event store is the SQLite recorder file BESIDE the .duckdb file —
    # never the analytics file itself (SQLite cannot open it). The shared
    # sidecar helper owns the name: get_app derives it through the same
    # path the migrate-data legs write to, so a verified migrate plus a
    # backend flip reopens the SAME store.
    app = get_app(conn_str, backend="duckdb")

    checkpoint_mgr = CheckpointManager(gateway=gateway)
    dlq_handler = DLQHandler(gateway=gateway)

    # Shared read path over the eventsourcing lib's events table (SQLite
    # recorder file on this backend — never a DuckDB table).
    event_reader = EventReader(None, app.mapper, events_table_name(app), recorder=app.recorder)

    embedder = create_embedder(cfg, None)
    embed_projection = EmbedChunksProjection(
        None, embedder, checkpoint_mgr, dlq_handler, gateway=gateway
    )
    docs_projection = DocumentsProjection(None, checkpoint_mgr, dlq_handler, gateway=gateway)

    # Surfaces: the HTTP app and socket server are storage-blind (routes
    # call the handler globals); handler globals stay unset until their
    # ports land, so those routes fail typed instead of at startup.
    from corpus_kb.api.http import create_http_app
    from corpus_kb.api.socket import get_socket_server

    logger.info("DuckDB research ecosystem initialized (handlers pending their port)")

    return {
        "pool": None,
        "gateway": gateway,
        "app": app,
        "embed_projection": embed_projection,
        "docs_projection": docs_projection,
        "event_reader": event_reader,
        "http_app": create_http_app(),
        "socket_server": get_socket_server(),
        "config": cfg,
        "rag_backend": None,
        "router_handler": None,
        "ui_mounted": False,
        "notebooks_mounted": False,
    }
