"""U48/P6: shadow-row writer, ADDITIVE to the live ingest path.

Replaces one document's shadow rows in ``shadow_chunks``/``shadow_vectors``
(migration 021) with a rebuilt tree — a derived, rebuildable artifact. The
v1 ``chunks``/``chunks_vectors`` rows stay the LIVE search corpus and are
never read or written here; every failure mode of the shadow write is
isolated by the caller (``ingest_common.run_pipeline`` logs and continues).
Shadow vectors follow the migration-016 contract: exactly 1024 dims (MRL-
sliced from >=1024-d embeddings, never padded); models below 1024 dims —
e.g. the default nomic-embed-text (768d) — skip the shadow VECTORS honestly
(the returned note says so) while the shadow TEXT rows still land.

One writer, both backends (storage portability program): the constructor
takes an asyncpg pool (wrapped in the Postgres gateway) or any SqlGateway.
DuckDB deltas, verified on 1.4.5: shadow_vectors has no ON DELETE CASCADE,
so the vector rows are deleted BEFORE the chunk rows; ``$12::jsonb`` is
``JSON``; arrays bind as one Python list per value (never per element); ids
bind as strings, not UUID objects.
"""

from __future__ import annotations

import json
import logging
from typing import Any, cast

import asyncpg

from corpus_kb.dimensions import RESEARCH_EMBEDDING_DIMENSIONS

from ..chunking.hierarchy_tree import (
    LEVEL_CHILD,
    LEVEL_CHUNK,
    LEVEL_PARENT,
    HierarchyTree,
)
from ..storage.gateway import SqlGateway
from ..storage.tenant_conn import tenant_connection
from .ingest_store import as_gateway, gateway_backend

logger = logging.getLogger(__name__)

# Insert order: parents, then chunks, then children, so the
# self-referencing parent_id FK always resolves.
_WRITE_LEVELS = (LEVEL_PARENT, LEVEL_CHUNK, LEVEL_CHILD)

_DELETE_VECTORS_SQL = (
    "DELETE FROM shadow_vectors WHERE tenant_id = $1 AND shadow_chunk_id IN "
    "(SELECT shadow_chunk_id FROM shadow_chunks WHERE tenant_id = $1 AND doc_id = $2)"
)
_DELETE_CHUNKS_SQL = "DELETE FROM shadow_chunks WHERE tenant_id = $1 AND doc_id = $2"
# DuckDB: no cascade and no parent-row rewrite — children go before parents.
_DELETE_CHUNKS_LEVEL_SQL = (
    "DELETE FROM shadow_chunks WHERE tenant_id = $1 AND doc_id = $2 AND level = $3"
)
# Stages of committed deletes, children first: vectors + leaf level, then
# the middle level, then the roots. Args ride with each stage because the
# vector and level statements take different parameter counts.
_DUCKDB_DELETE_STAGES = (
    ((_DELETE_VECTORS_SQL, None),),
    ((_DELETE_CHUNKS_LEVEL_SQL, LEVEL_CHILD),),
    ((_DELETE_CHUNKS_LEVEL_SQL, LEVEL_CHUNK),),
    ((_DELETE_CHUNKS_LEVEL_SQL, LEVEL_PARENT),),
)

_CHUNK_INSERT_PG = """
    INSERT INTO shadow_chunks (tenant_id, doc_id, level, seq, parent_id,
        text, token_count, start_char, end_char, boundary, path, metadata)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb)
    RETURNING shadow_chunk_id::text
"""
_CHUNK_INSERT_DUCKDB = """
    INSERT INTO shadow_chunks (tenant_id, doc_id, level, seq, parent_id,
        text, token_count, start_char, end_char, boundary, path, metadata)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::JSON)
    RETURNING shadow_chunk_id::text
"""

_VECTOR_UPSERT_PG = """
    INSERT INTO shadow_vectors (shadow_chunk_id, tenant_id, embedding,
        embedding_model, dimensions)
    VALUES ($1, $2, $3::vector, $4, $5)
    ON CONFLICT (shadow_chunk_id) DO UPDATE
    SET embedding = $3::vector, embedding_model = $4,
        dimensions = $5, embedded_at = NOW()
"""
_W_V1024 = str(RESEARCH_EMBEDDING_DIMENSIONS)
_VECTOR_UPSERT_DUCKDB = f"""
    INSERT INTO shadow_vectors (shadow_chunk_id, tenant_id, embedding,
        embedding_model, dimensions)
    VALUES ($1, $2, $3::FLOAT[{_W_V1024}], $4, $5)
    ON CONFLICT (shadow_chunk_id) DO UPDATE
    SET embedding = $3::FLOAT[{_W_V1024}], embedding_model = $4,
        dimensions = $5, embedded_at = NOW()
"""


class ShadowIngestWriter:
    """Shadow hierarchy writes over one SqlGateway (Postgres or DuckDB)."""

    def __init__(self, pool: Any, tenant_id: str) -> None:
        self._pool: Any = pool
        self._gateway: SqlGateway = as_gateway(pool)
        self._tenant_id = tenant_id
        self._duckdb = gateway_backend(self._gateway) == "duckdb"

    async def store_tree(
        self,
        tree: HierarchyTree,
        vectors_1024: dict[str, list[float]] | None = None,
        embedding_model: str = "nomic-embed-text",
        metadata_by_key: dict[str, dict[str, object]] | None = None,
    ) -> tuple[int, int]:
        """Replace this document's shadow rows with the rebuilt tree.

        Rows are deleted and reinserted wholesale (no v1-style hash dedup),
        in one scope per backend. Parents are inserted before chunks,
        chunks before children, so the self-referencing ``parent_id`` FK
        always resolves. Returns (chunk_count, vector_count).
        """
        parents_first = [node for level in _WRITE_LEVELS for node in tree.by_level(level)]
        key_to_id: dict[str, str] = {}
        chunk_count = 0
        vector_count = 0
        doc_id = tree.doc_id
        chunk_sql = _CHUNK_INSERT_DUCKDB if self._duckdb else _CHUNK_INSERT_PG
        # shadow_chunks/shadow_vectors are RLS FORCED (migration 021), so the
        # tenant context lives inside the SAME scope as the writes.
        if self._duckdb:
            # DuckDB's FK checks consult the last COMMITTED state, so each
            # generation goes in its OWN committed scope, children first
            # (vectors -> child -> chunk -> parent). Shadow rows are a
            # derived, rebuildable artifact — the staged split is safe.
            for stage in _DUCKDB_DELETE_STAGES:
                async with self._gateway.transaction(self._tenant_id) as tx:
                    for stage_sql, level in stage:
                        if level is None:
                            await tx.execute(stage_sql, self._tenant_id, doc_id)
                        else:
                            await tx.execute(stage_sql, self._tenant_id, doc_id, level)
        else:
            # ON DELETE CASCADE takes shadow_vectors with it.
            async with self._scope() as conn:
                await conn.execute(_DELETE_CHUNKS_SQL, self._tenant_id, doc_id)
        async with self._scope() as conn:
            for node in parents_first:
                shadow_id = await conn.fetchval(
                    chunk_sql,
                    self._tenant_id,
                    doc_id,
                    node.level,
                    node.seq,
                    key_to_id[node.parent_key] if node.parent_key else None,
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
                embedding: Any = _vector_literal(vector)
                await conn.execute(
                    _VECTOR_UPSERT_DUCKDB if self._duckdb else _VECTOR_UPSERT_PG,
                    key_to_id[key],
                    self._tenant_id,
                    embedding,
                    embedding_model,
                    len(vector),
                )
                vector_count += 1
        return chunk_count, vector_count

    def _scope(self) -> Any:
        """One transaction scope with the tenant context set (per backend)."""
        if self._duckdb:
            return self._gateway.transaction(self._tenant_id)
        return tenant_connection(cast("asyncpg.Pool", self._pool), self._tenant_id)


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(str(v) for v in vector) + "]"
