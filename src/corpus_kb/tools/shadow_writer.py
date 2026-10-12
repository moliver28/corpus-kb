"""U48/P6: Postgres shadow-row writer (ADDITIVE to the live path).

Replaces one document's shadow rows in ``shadow_chunks``/``shadow_vectors``
(migration 021) with a rebuilt tree — a derived, rebuildable artifact. The
v1 ``chunks``/``chunks_vectors`` rows stay the LIVE search corpus and are
never read or written here; every failure mode of the shadow write is
isolated by the caller (``ingest_common.run_pipeline`` logs and continues).
Shadow vectors follow the migration-016 contract: exactly 1024 dims (MRL-
sliced from >=1024-d embeddings, never padded); models below 1024 dims —
e.g. the default nomic-embed-text (768d) — skip the shadow VECTORS honestly
(the returned note says so) while the shadow TEXT rows still land.
"""

from __future__ import annotations

import json
from uuid import UUID

import asyncpg

from ..chunking.hierarchy_tree import (
    LEVEL_CHILD,
    LEVEL_CHUNK,
    LEVEL_PARENT,
    HierarchyTree,
)
from ..storage.tenant_conn import tenant_connection

# Insert order: parents, then chunks, then children, so the
# self-referencing parent_id FK always resolves.
_WRITE_LEVELS = (LEVEL_PARENT, LEVEL_CHUNK, LEVEL_CHILD)


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
