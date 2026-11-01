"""U48/P6: side-by-side shadow hierarchy builder + orchestrator (ADDITIVE).

Owns the build-and-embed half of the shadow ingest pipeline; the Postgres
shadow-row writes live in ``shadow_writer.ShadowIngestWriter``:

* build the deterministic three-level tree for the document, grouping
  segments by the source's NATURAL boundaries: the segment adapter is
  picked per document by the resolved ingestion strategy (decks group by
  slide, transcripts by speaker turn / qa_pair, everything else by
  heading) and gated by ``ingest.chunking.respect_boundaries``;
* embed the retrieval levels and slice them to the migration-016 vector
  contract (1024 dims), then hand tree + vectors to the writer.

The v1 ``chunks``/``chunks_vectors`` rows stay the LIVE search corpus and
are never read or written here; every failure mode of the shadow write is
isolated by the caller (``ingest_common.run_pipeline`` logs and continues).
Shadow vectors follow the migration-016 contract: exactly 1024 dims (MRL-
sliced from >=1024-d embeddings, never padded); models below 1024 dims —
e.g. the default nomic-embed-text (768d) — skip the shadow VECTORS honestly
(the returned note says so) while the shadow TEXT rows still land.
"""

from __future__ import annotations

import logging
from typing import Protocol

import asyncpg

from corpus_kb.chunking.hierarchy_settings import HierarchySettings
from corpus_kb.dimensions import RESEARCH_EMBEDDING_DIMENSIONS

from ..chunking.hierarchy_tree import (
    LEVEL_CHILD,
    LEVEL_CHUNK,
    HierarchyTree,
)
from ..storage.gateway import SqlGateway
from ..utils.models import Chunk
from .shadow_adapters import (
    ADAPTER_HEADING,
    ADAPTER_SLIDES,
    ADAPTER_TURNS,
    build_main_path_tree,
    select_segment_adapter,
)
from .shadow_writer import ShadowIngestWriter

SHADOW_VECTOR_DIM = RESEARCH_EMBEDDING_DIMENSIONS

logger = logging.getLogger(__name__)


class EmbedChunksFn(Protocol):
    """The ``ingest_common.embed_chunks`` callable shape."""

    async def __call__(
        self,
        chunks: list[Chunk],
        config: dict[str, object],
        pool: asyncpg.Pool | None = None,
    ) -> tuple[bool, str | None]:
        """Embed chunk texts; returns (degraded, error)."""
        ...


def _v1_link_metadata(
    tree: HierarchyTree,
    chunks: list[Chunk],
) -> dict[str, dict[str, object]]:
    """Map each shadow CHILD to the v1 chunk containing its start char.

    Query-time parent expansion resolves a live v1 hit through this link
    (metadata 'v1_chunk_id') without touching the live path.
    """
    if not chunks:
        return {}
    ordered = sorted(
        (
            (chunk.source_start_char if chunk.source_start_char is not None else 0, chunk)
            for chunk in chunks
        ),
        key=lambda pair: pair[0],
    )
    metadata: dict[str, dict[str, object]] = {}
    for child in tree.by_level(LEVEL_CHILD):
        chosen: Chunk | None = None
        for start, chunk in ordered:
            if start <= child.start_char:
                chosen = chunk
            else:
                break
        if chosen is not None:
            metadata[child.key] = {"v1_chunk_id": str(chosen.chunk_id)}
    return metadata


async def write_shadow_hierarchy(
    pool: asyncpg.Pool | SqlGateway,
    tenant_id: str,
    document_id: str,
    text: str,
    chunks: list[Chunk],
    config: dict[str, object],
    settings: HierarchySettings,
    embed_chunks_fn: EmbedChunksFn | None = None,
    strategy: str | None = None,
) -> tuple[int, int, str | None]:
    """Build + store the side-by-side shadow hierarchy for one document.

    ``embed_chunks_fn`` injects the pipeline's embedder (the
    ``ingest_common.embed_chunks`` signature) — lazy-imported by default so
    this module never drags the extraction/embedder stack into import.
    ``strategy`` is the resolved ingestion-strategy name; it (or the plan's
    boundary markers) picks the segment adapter, recorded in the debug log
    and appended to any shadow note. Returns (chunk_count, vector_count,
    note).
    """
    embed_pool = pool if isinstance(pool, asyncpg.Pool) else None
    if embed_chunks_fn is None:
        from .ingest_common import embed_chunks as embed_chunks_fn_default

        embed_chunks_fn = embed_chunks_fn_default

    adapter = select_segment_adapter(chunks, strategy)
    logger.debug(
        "shadow hierarchy boundary adapter %r (strategy=%r, doc=%s)",
        adapter,
        strategy,
        document_id,
    )
    tree = build_main_path_tree(document_id, text, chunks, settings, adapter)

    # Retrieval levels are child (128) + chunk (512); parents are reached
    # through parent_id (small-to-big) and carry no vector.
    retrieval_nodes = [
        *tree.by_level(LEVEL_CHILD),
        *tree.by_level(LEVEL_CHUNK),
    ]
    shadow_models = [
        Chunk(chunk_id=node.key, document_id=document_id, text=node.text, source_type="text")
        for node in retrieval_nodes
    ]
    vectors_1024: dict[str, list[float]] = {}
    note: str | None = None
    if shadow_models:
        degraded, embed_err = await embed_chunks_fn(shadow_models, config, pool=embed_pool)
        first_vector = next((m.embedding for m in shadow_models if m.embedding is not None), None)
        dims = len(first_vector) if first_vector is not None else 0
        if dims < SHADOW_VECTOR_DIM:
            note = (
                f"shadow vectors skipped: embedding dims {dims} < {SHADOW_VECTOR_DIM} "
                "(migration-016 contract: native or MRL-sliced from >=1024)"
            )
        elif degraded:
            note = f"shadow vectors skipped: embedder degraded ({embed_err})"
        else:
            from ..rag.embedder import _slice_normalize

            for model in shadow_models:
                if model.embedding is not None:
                    vectors_1024[model.chunk_id] = _slice_normalize(
                        model.embedding, SHADOW_VECTOR_DIM
                    )

    embedding_model = str(_config_embedding_model(config))
    chunk_count, vector_count = await ShadowIngestWriter(pool, tenant_id).store_tree(
        tree,
        vectors_1024 or None,
        embedding_model=embedding_model,
        metadata_by_key=_v1_link_metadata(tree, chunks),
    )
    if note is not None:
        note = f"{note}; boundary adapter: {adapter}"
    return chunk_count, vector_count, note


def _config_embedding_model(config: dict[str, object]) -> str:
    embedding = config.get("embedding")
    if isinstance(embedding, dict):
        model = embedding.get("model")
        if isinstance(model, str):
            return model
    return "nomic-embed-text"


__all__ = [
    "ADAPTER_HEADING",
    "ADAPTER_SLIDES",
    "ADAPTER_TURNS",
    "SHADOW_VECTOR_DIM",
    "EmbedChunksFn",
    "ShadowIngestWriter",
    "build_main_path_tree",
    "select_segment_adapter",
    "write_shadow_hierarchy",
]
