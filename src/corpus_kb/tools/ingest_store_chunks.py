"""Module functions for the chunk and vector write paths (W4-1 split).

The DuckDB chunk-write loop, the vector write, and the vector bind-row
builder moved here verbatim from ``PostgresIngestStore`` so that file
stays under the 250-line soft limit. The store methods build their rows
and delegate here; the ChunkSink seam keeps its byte-identical shapes.
"""

from __future__ import annotations

import json
from typing import Any, NamedTuple

from corpus_kb.errors import ConfigError
from corpus_kb.storage.gateway import SqlGateway, gateway_backend
from corpus_kb.utils.models import Chunk

from . import ingest_batch
from .ingest_sql import (
    IngestSql,
    duckdb_vector_width_probe,
    ingest_sql_for,
    pg_vector_type_mod_probe,
    vector_width_config_error,
    vector_write_params,
)

_CHUNK_TOMBSTONE_SQL = (
    "UPDATE chunks SET tombstoned_at = NOW() WHERE tenant_id = $1 AND doc_id = $2 "
    "AND chunk_index >= $3 AND tombstoned_at IS NULL"
)
_CHUNK_DEGRADED_SQL = "UPDATE chunks SET degraded = $3 WHERE chunk_id = $1 AND tenant_id = $2"

# DuckDB: a chunks row referenced by ANY child row (vectors, relations,
# topics, labels, predictions) cannot be rewritten (an UPDATE rewrites the
# row), so a changed or tombstoned chunk's dependents go first. Every one of
# these child rows is derived/replaceable per the migrations that add them.
_DUCKDB_DEPENDENT_TABLES = (
    "chunks_vectors",
    "relations",
    "chunk_topics",
    "chunk_labels",
    "chunk_predictions",
)


class VectorRow(NamedTuple):
    """One chunk's vector bind row plus the inputs the write path needs."""

    params: tuple[object, ...]
    degraded: bool
    chunk_id: str
    embedding_model: str
    dimensions: int


def vector_row(
    tenant_id: str,
    chunk: Chunk,
    embedding_model: str,
    dimensions: int,
    vectors_1024: dict[str, list[float]],
) -> VectorRow:
    """One chunk's vector bind row: (params, degraded flag)."""
    v1024 = vectors_1024.get(str(chunk.chunk_id))
    degraded = all(v == 0.0 for v in chunk.embedding) if chunk.embedding else True
    assert chunk.embedding is not None
    embedding_param, v1024_param = vector_write_params(chunk.embedding, v1024)
    params = (
        chunk.chunk_id,
        tenant_id,
        embedding_param,
        v1024_param,
        embedding_model,
        dimensions,
        degraded,
    )
    return VectorRow(params, degraded, chunk.chunk_id, embedding_model, dimensions)


async def _probe_vector_width(
    sql: IngestSql, tx: SqlGateway, dimensions: int, embedding_model: str
) -> None:
    if sql.is_duckdb:
        await duckdb_vector_width_probe(tx, dimensions, embedding_model)
    else:
        await pg_vector_type_mod_probe(tx, dimensions, embedding_model)


async def store_vectors(gateway: SqlGateway, tenant_id: str, rows: list[VectorRow]) -> int:
    """Insert prepared vector rows. vectors_1024 rides inside each row.

    Re-embed OVERWRITES: ``vector_1024`` is replaced, never COALESCEd with
    the stored slice, so a re-embed cannot leave a stale matryoshka slice
    next to a fresh vector. Each write also states the row's ``degraded``
    flag honestly (all-zero vectors are the embedder-failure marker) and
    mirrors it onto the owning chunks row.

    Width guard: the embedding column is probed ONCE up front (Postgres
    catalog typmod, DuckDB ``PRAGMA table_info``) and any width error
    inside the loop is re-raised as the same typed ConfigError — a
    model/dimension mismatch is a config problem, not a write failure.
    """
    sql = ingest_sql_for(gateway_backend(gateway))
    params = [row.params for row in rows]
    degraded_flags = [row.degraded for row in rows]
    async with gateway.transaction(tenant_id) as tx:
        if rows:
            await _probe_vector_width(sql, tx, rows[0].dimensions, rows[0].embedding_model)
        try:
            if params:
                await tx.executemany(sql.vector_insert_sql, params)
                if sql.is_duckdb:
                    await tx.executemany(
                        _CHUNK_DEGRADED_SQL,
                        [
                            (row.chunk_id, tenant_id, degraded)
                            for row, degraded in zip(rows, degraded_flags, strict=False)
                        ],
                    )
                else:
                    await ingest_batch.mirror_degraded(
                        tx, [row.chunk_id for row in rows], degraded_flags
                    )
        except ConfigError:
            raise
        except Exception as exc:
            raise vector_width_config_error(exc, rows[0].embedding_model) from exc
    return len(rows)


async def _delete_chunk_dependents(tx: SqlGateway, where: str, *args: Any) -> None:
    """Drop a chunk's derived child rows (DuckDB pre-rewrite step)."""
    for table in _DUCKDB_DEPENDENT_TABLES:
        await tx.execute(f"DELETE FROM {table} {where}", *args)


async def store_chunks_duckdb(gateway: SqlGateway, tenant_id: str, chunks: list[Chunk]) -> set[str]:
    """Per-row chunk write path (DuckDB): FK commit-visibility pre-pass."""
    needs_embedding: set[str] = set()
    doc_id = chunks[0].document_id
    async with gateway.transaction(tenant_id) as tx:
        for count, chunk in enumerate(chunks):
            chunk_index = chunk.sibling_order if chunk.sibling_order is not None else count
            row = await tx.fetchrow(
                "SELECT chunk_id, chunk_hash FROM chunks "
                "WHERE tenant_id = $1 AND doc_id = $2 AND chunk_index = $3",
                tenant_id,
                chunk.document_id,
                chunk_index,
            )
            if row is not None and row["chunk_hash"] != chunk.chunk_hash:
                await _delete_chunk_dependents(tx, "WHERE chunk_id = $1", row["chunk_id"])
        await _delete_chunk_dependents(
            tx,
            "WHERE tenant_id = $1 AND chunk_id IN (SELECT chunk_id FROM chunks "
            "WHERE tenant_id = $1 AND doc_id = $2 AND chunk_index >= $3)",
            tenant_id,
            doc_id,
            len(chunks),
        )
    async with gateway.transaction(tenant_id) as tx:
        for count, chunk in enumerate(chunks):
            chunk_index = chunk.sibling_order if chunk.sibling_order is not None else count
            existing = await tx.fetchrow(
                "SELECT chunk_id, chunk_hash FROM chunks "
                "WHERE tenant_id = $1 AND doc_id = $2 AND chunk_index = $3",
                tenant_id,
                chunk.document_id,
                chunk_index,
            )
            if existing is None:
                row = await tx.fetchrow(
                    """
                    INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index,
                        text, source_type, entity_name, heading_path, file_path,
                        start_line, end_line, metadata, chunk_hash, source_timestamp,
                        context_blurb)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                    ON CONFLICT (tenant_id, doc_id, chunk_index) DO NOTHING
                    RETURNING chunk_id
                    """,
                    chunk.chunk_id,
                    tenant_id,
                    chunk.document_id,
                    chunk_index,
                    chunk.text,
                    chunk.source_type,
                    chunk.entity_name,
                    json.dumps(chunk.heading_path) if chunk.heading_path else None,
                    chunk.metadata.get("file_path") if chunk.metadata else None,
                    chunk.start_line,
                    chunk.end_line,
                    json.dumps(chunk.metadata),
                    chunk.chunk_hash,
                    chunk.source_timestamp,
                    chunk.context_blurb,
                )
                if row is not None:
                    needs_embedding.add(str(row["chunk_id"]))
            elif existing["chunk_hash"] != chunk.chunk_hash:
                await tx.execute(
                    """
                    UPDATE chunks SET text = $1, chunk_hash = $2, source_timestamp = $3,
                        context_blurb = $4, metadata = $5, superseded_at = NULL,
                        tombstoned_at = NULL
                    WHERE chunk_id = $6
                    """,
                    chunk.text,
                    chunk.chunk_hash,
                    chunk.source_timestamp,
                    chunk.context_blurb,
                    json.dumps(chunk.metadata),
                    existing["chunk_id"],
                )
                chunk.chunk_id = str(existing["chunk_id"])
                needs_embedding.add(str(existing["chunk_id"]))
            else:
                # Hash unchanged, no-op. DuckDB: relations built later in
                # this run carry the in-memory chunk_id, so it is
                # re-pointed at the stored row (extractors key
                # relations on chunk.chunk_id).
                chunk.chunk_id = str(existing["chunk_id"])
        await tx.execute(
            _CHUNK_TOMBSTONE_SQL,
            tenant_id,
            chunks[0].document_id if chunks else None,
            len(chunks),
        )
    return needs_embedding
