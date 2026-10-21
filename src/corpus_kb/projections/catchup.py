"""The ONE projection catch-up for every surface.

Every command that writes events must advance ALL read models through
:meth:`advance_all_projections`: the research projection, the documents
projection, and the chunk-embedding projection. Previously the CLI caught
these up itself while the shared tool layer (MCP/HTTP) skipped them or
advanced only research — the same operation had different post-conditions
per surface. The CLI now shares these tool functions, so every surface
lands identical projections exactly once.
"""

from __future__ import annotations

from uuid import UUID

import asyncpg

from corpus_kb.storage.gateway import SqlGateway


async def advance_all_projections(
    pool: asyncpg.Pool, tenant_id: UUID, gateway: SqlGateway | None = None
) -> None:
    """Advance research + documents + embeds projections to the event head.

    Reads go through the recorder API (program §7: the event store is
    lib-owned — a SQLite file on the duckdb backend), so the same reader
    serves both backends. ``gateway`` swaps the projection write side; the
    research leg still takes the pool until its own port lands (§13 owns
    that seam), so ``gateway`` stays optional and Postgres keeps its
    resolved ``PostgresGateway`` default.
    """
    from corpus_kb.config import load_config
    from corpus_kb.domain.application import events_table_name
    from corpus_kb.handlers.research_handler import get_research_handler
    from corpus_kb.projections.checkpoint import CheckpointManager
    from corpus_kb.projections.dlq import DLQHandler
    from corpus_kb.projections.documents_projection import DocumentsProjection
    from corpus_kb.projections.embed_projection import EmbedChunksProjection
    from corpus_kb.projections.event_reader import EventReader
    from corpus_kb.projections.research._embed import ResearchEmbedder
    from corpus_kb.projections.research_projection import ResearchProjection
    from corpus_kb.rag import create_embedder

    cfg = load_config()
    app = get_research_handler(pool).app
    reader = EventReader(pool, app.mapper, events_table_name(app), recorder=app.recorder)
    checkpoint = CheckpointManager(pool, gateway=gateway)
    dlq = DLQHandler(pool, gateway=gateway)
    embedder = ResearchEmbedder(pool, create_embedder(cfg, pool))
    await ResearchProjection(pool, checkpoint, dlq, embedder).catch_up(reader)
    await DocumentsProjection(pool, checkpoint, dlq, gateway=gateway).catch_up(reader, tenant_id)
    await EmbedChunksProjection(
        pool, create_embedder(cfg, pool), checkpoint, dlq, gateway=gateway
    ).catch_up(reader, tenant_id)
