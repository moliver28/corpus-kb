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


async def advance_all_projections(pool: asyncpg.Pool, tenant_id: UUID) -> None:
    """Advance research + documents + embeds projections to the event head."""
    from corpus_kb.config import load_config
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
    reader = EventReader(pool, app.mapper, app.recorder.events_table_name)
    checkpoint = CheckpointManager(pool)
    dlq = DLQHandler(pool)
    embedder = ResearchEmbedder(pool, create_embedder(cfg, pool))
    await ResearchProjection(pool, checkpoint, dlq, embedder).catch_up(reader)
    await DocumentsProjection(pool, checkpoint, dlq).catch_up(reader, tenant_id)
    await EmbedChunksProjection(pool, create_embedder(cfg, pool), checkpoint, dlq).catch_up(
        reader, tenant_id
    )
