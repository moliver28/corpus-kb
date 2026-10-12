"""U48/P6: side-by-side shadow hierarchy writer (ADDITIVE to the live path).

Owns the two shadow-side responsibilities of the ingest pipeline:

* build the deterministic three-level tree for the document (main RAG
  path: Unstructured elements, heading-grouped);
* replace the document's shadow rows in ``shadow_chunks``/``shadow_vectors``
  (migration 021) with the rebuilt tree — a derived, rebuildable artifact.

The v1 ``chunks``/``chunks_vectors`` rows stay the LIVE search corpus and
are never read or written here; every failure mode of the shadow write is
isolated by the caller (``ingest_common.run_pipeline`` logs and continues).
Shadow vectors follow the migration-016 contract: exactly 1024 dims
(MRL-sliced from >=1024-d embeddings, never padded); models below 1024
dims — e.g. the default nomic-embed-text (768d) — skip the shadow VECTORS
honestly (the returned note says so) while the shadow TEXT rows still land.
"""

from __future__ import annotations

import json
from typing import Protocol
from uuid import UUID

import asyncpg

from ..chunking.hierarchy_paths import segments_from_chunks
from ..chunking.hierarchy_tree import (
    LEVEL_CHILD,
    LEVEL_CHUNK,
    LEVEL_PARENT,
    HierarchyTree,
    build_hierarchy,
)
from ..ingest.hierarchy_settings import HierarchySettings
from ..storage.tenant_conn import tenant_connection
from ..utils.models import Chunk

# Insert order: parents, then chunks, then children, so the
# self-referencing parent_id FK always resolves.
_WRITE_LEVELS = (LEVEL_PARENT, LEVEL_CHUNK, LEVEL_CHILD)

SHADOW_VECTOR_DIM = 1024


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


class ShadowIngestWriter:
    """Postgres writes for the shadow hierarchy (asyncpg + tenant GUC)."""

    def __init__(self, pool: asyncpg.Pool, tenant_id: str) -> None:
        self._pool = pool
        self._tenant_id = tenant_id

    async def store_tree(
        self,
        tree: HierarchyTree,
        vectors_1024: dict[str, list[float]] | None = None,
        embedding_model: str = "nomic-embed-text",
        metadata_by_key: dict[str, dict[str, object]] | None = None,
    ) -> tuple[int, int]:
        """Replace this document's shadow rows with the rebuilt tree.

        Rows are deleted and reinserted wholesale (no v1-style hash dedup),
        inside ONE transaction. Parents are inserted before chunks, chunks
        before children, so the self-referencing ``parent_id`` FK always
        resolves. Returns (chunk_count, vector_count).
        """
        parents_first = [node for level in _WRITE_LEVELS for node in tree.by_level(level)]
        key_to_id: dict[str, str] = {}
        chunk_count = 0
        vector_count = 0
        # shadow_chunks/shadow_vectors are RLS FORCED (migration 021), so the
        # tenant GUC must live inside the SAME transaction as the writes —
        # exactly the storage/tenant_conn.py house contract.
        async with tenant_connection(self._pool, self._tenant_id) as conn:
            # ON DELETE CASCADE takes shadow_vectors with it.
            await conn.execute(
                "DELETE FROM shadow_chunks WHERE tenant_id = $1 AND doc_id = $2",
                self._tenant_id,
                UUID(tree.doc_id),
            )
            for node in parents_first:
                shadow_id = await conn.fetchval(
                    """
                    INSERT INTO shadow_chunks (tenant_id, doc_id, level, seq, parent_id,
                        text, token_count, start_char, end_char, boundary, path, metadata)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb)
                    RETURNING shadow_chunk_id::text
                    """,
                    self._tenant_id,
                    UUID(tree.doc_id),
                    node.level,
                    node.seq,
                    UUID(key_to_id[node.parent_key]) if node.parent_key else None,
                    node.text,
                    node.token_count,
                    node.start_char,
                    node.end_char,
                    node.boundary,
                    tree.path,
                    json.dumps(
                        {
                            "key": node.key,
                            "child_keys": list(node.child_keys),
                            **(metadata_by_key or {}).get(node.key, {}),
                        }
                    ),
                )
                key_to_id[node.key] = str(shadow_id)
                chunk_count += 1
            for key, vector in (vectors_1024 or {}).items():
                if key not in key_to_id:
                    continue
                vector_str = "[" + ",".join(str(v) for v in vector) + "]"
                await conn.execute(
                    """
                    INSERT INTO shadow_vectors (shadow_chunk_id, tenant_id, embedding,
                        embedding_model, dimensions)
                    VALUES ($1, $2, $3::vector, $4, $5)
                    ON CONFLICT (shadow_chunk_id) DO UPDATE
                    SET embedding = $3::vector, embedding_model = $4,
                        dimensions = $5, embedded_at = NOW()
                    """,
                    UUID(key_to_id[key]),
                    self._tenant_id,
                    vector_str,
                    embedding_model,
                    len(vector),
                )
                vector_count += 1
        return chunk_count, vector_count


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


def build_main_path_tree(
    document_id: str,
    text: str,
    chunks: list[Chunk],
    settings: HierarchySettings,
) -> HierarchyTree:
    """Deterministic tree for the main RAG path (heading-grouped elements)."""
    segments = segments_from_chunks(chunks, text)
    return build_hierarchy(
        document_id,
        segments,
        settings,
        path="main",
        group_kind="heading",
    )


async def write_shadow_hierarchy(
    pool: asyncpg.Pool,
    tenant_id: str,
    document_id: str,
    text: str,
    chunks: list[Chunk],
    config: dict[str, object],
    settings: HierarchySettings,
    embed_chunks_fn: EmbedChunksFn | None = None,
) -> tuple[int, int, str | None]:
    """Build + store the side-by-side shadow hierarchy for one document.

    ``embed_chunks_fn`` injects the pipeline's embedder (the
    ``ingest_common.embed_chunks`` signature) — lazy-imported by default so
    this module never drags the extraction/embedder stack into import.
    Returns (chunk_count, vector_count, note).
    """
    if embed_chunks_fn is None:
        from .ingest_common import embed_chunks as embed_chunks_fn_default

        embed_chunks_fn = embed_chunks_fn_default

    tree = build_main_path_tree(document_id, text, chunks, settings)

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
        degraded, embed_err = await embed_chunks_fn(shadow_models, config, pool=pool)
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
    return chunk_count, vector_count, note


def _config_embedding_model(config: dict[str, object]) -> str:
    embedding = config.get("embedding")
    if isinstance(embedding, dict):
        model = embedding.get("model")
        if isinstance(model, str):
            return model
    return "nomic-embed-text"
