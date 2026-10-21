"""Small CRUD-ish query handlers (zero-drift split of query_handler.py).

Pure move of QueryHandler.handle_list_documents, handle_search_similar, and
handle_search_context as module functions; QueryHandler keeps thin
delegating methods so every surface (HTTP/socket/CLI/tests) keeps its exact
call shape. SQL and logic are byte-identical to the pre-split methods. The
non-search reads (``sql_query``, ``list_entities``, ``verify_answer``) live
in :mod:`query_crud_reads` (a pure move for the 250-line rule).

Tenant-context note: every pool-path handler acquires through
:func:`storage.tenant_conn.tenant_connection` (acquire → transaction →
set_config → read). The pre-wave shapes issued ``set_config`` on an
autocommit connection, where the transaction-local setting evaporates
before the read; any database whose ``app.current_tenant_id`` default is
empty then failed with ``invalid input syntax for type uuid: ""`` inside
the RLS policy. Fixed in the tenant-correctness fix, not kept
bug-compatible: the broken shape never worked on a strict database.

Storage: each function takes an ``asyncpg.Pool`` (legacy wiring — the
shipped Postgres shapes, byte-identical) or a
:class:`~corpus_kb.storage.gateway.SqlGateway` (portable wiring — the
DuckDB arm, where tenancy rides the explicit ``tenant_id`` filters inside
the gateway's ``transaction(tenant_id)`` scope). ``search_similar`` is
dual-engine: Postgres keeps the pgvector ``<=>`` shape; the DuckDB arm
serves the same contract from the mirror schema via
``storage.duckdb_similar`` (full parity, program V1 goal).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg

from corpus_kb.domain.models import (
    DocumentResult,
    ListDocumentsQuery,
    SearchContextQuery,
    SearchQuery,
    SearchResult,
    SearchSimilarQuery,
)
from corpus_kb.handlers.query_integrity import degraded_candidate_predicate
from corpus_kb.storage.duckdb_gateway import DuckDbGateway
from corpus_kb.storage.duckdb_similar import similar_rows
from corpus_kb.storage.gateway import SqlGateway, is_gateway
from corpus_kb.storage.tenant_conn import tenant_connection


async def list_documents(
    pool: asyncpg.Pool | SqlGateway, query: ListDocumentsQuery
) -> list[DocumentResult]:
    """List documents with pagination."""
    args = (str(query.tenant_id), query.limit, query.offset)
    if is_gateway(pool):
        # created_at crosses as an ISO string on the gateway arm: DuckDB
        # needs the optional pytz wheel for TIMESTAMPTZ -> Python.
        # chunk_count is COMPUTED from chunks: the DuckDB write path
        # leaves the documents column at its begin-run zero (the row is
        # written once), so the stored value would read 0 forever.
        sql = """
            SELECT doc_id, source, source_type,
                   (SELECT count(*) FROM chunks
                     WHERE chunks.doc_id = documents.doc_id) AS chunk_count,
                   created_at::VARCHAR AS created_at
            FROM documents
            WHERE tenant_id = $1
            ORDER BY created_at DESC, doc_id ASC
            LIMIT $2 OFFSET $3
            """
        async with pool.transaction(str(query.tenant_id)) as reader:
            rows = await reader.fetch(sql, *args)
        return _documents(rows)
    sql = """
        SELECT doc_id, source, source_type, chunk_count, created_at
        FROM documents
        WHERE tenant_id = $1
        ORDER BY created_at DESC
        LIMIT $2 OFFSET $3
        """
    async with tenant_connection(pool, str(query.tenant_id)) as conn:
        rows = await conn.fetch(sql, *args)
        return _documents(rows)


def _documents(rows: list[Any]) -> list[DocumentResult]:
    return [
        DocumentResult(
            doc_id=row["doc_id"],
            source=row["source"],
            source_type=row["source_type"],
            chunk_count=row["chunk_count"],
            created_at=str(row["created_at"]),
        )
        for row in rows
    ]


async def search_similar(
    pool: asyncpg.Pool | SqlGateway, *, exclude_degraded: bool, query: SearchSimilarQuery
) -> list[SearchResult]:
    """Find chunks similar to a given chunk via vector distance.

    Both engines serve the SAME contract (k nearest, zero-vector rows
    last, chunk_id tiebreaker, degraded candidates filtered, degraded
    seeds still discoverable). Postgres keeps the pgvector ``<=>`` SQL
    byte-identical; the DuckDB gateway scans the mirror schema exactly
    (:mod:`corpus_kb.storage.duckdb_similar`).
    """
    if isinstance(pool, DuckDbGateway):
        async with pool.transaction(str(query.tenant_id)) as reader:
            rows = await similar_rows(
                reader,
                chunk_id=str(query.chunk_id),
                tenant_id=str(query.tenant_id),
                k=query.k,
                degraded_pred=degraded_candidate_predicate(exclude_degraded),
            )
        return _similar_results(rows)
    async with tenant_connection(pool, query.tenant_id) as conn:
        # U40: ANN scan inside a MATERIALIZED CTE, exact ordering applied
        # outside over the retrieved set (`score + 0`, per pgvector docs).
        # Stage 3: degraded rows are excluded from CANDIDATES only — the
        # seed subqueries (SELECT embedding ... WHERE chunk_id = $1) stay
        # unfiltered so a degraded seed document remains discoverable.
        degraded_pred = degraded_candidate_predicate(exclude_degraded)
        rows = await conn.fetch(
            f"""
            WITH candidate AS MATERIALIZED (
                SELECT cv.chunk_id AS candidate_chunk_id,
                       1 - (cv.embedding <=> (
                           SELECT embedding FROM chunks_vectors WHERE chunk_id = $1
                       )) AS score
                FROM chunks_vectors cv
                WHERE cv.tenant_id = $2 AND cv.chunk_id != $1 {degraded_pred}
                ORDER BY cv.embedding <=> (
                    SELECT embedding FROM chunks_vectors WHERE chunk_id = $1
                )
                LIMIT $3
            )
            SELECT c.chunk_id, c.text, c.doc_id, d.source,
                   c.file_path, c.start_line, c.end_line, c.chunk_index, c.heading_path,
                   candidate.score AS score
            FROM candidate
            JOIN chunks c ON candidate.candidate_chunk_id = c.chunk_id
            JOIN documents d ON c.doc_id = d.doc_id
            WHERE c.tombstoned_at IS NULL AND c.superseded_at IS NULL
            ORDER BY candidate.score + 0 DESC, c.chunk_id ASC
            LIMIT $3
            """,
            str(query.chunk_id),
            str(query.tenant_id),
            query.k,
        )
        return _similar_results(rows)


def _similar_results(rows: list[Any]) -> list[SearchResult]:
    """Row -> SearchResult mapping shared by both engine arms."""
    return [
        SearchResult(
            chunk_id=row["chunk_id"],
            text=row["text"],
            score=row["score"],
            source=row["source"],
            doc_id=row["doc_id"],
            file_path=row["file_path"],
            start_line=row["start_line"],
            end_line=row["end_line"],
            chunk_index=row["chunk_index"],
            heading_path=json.loads(row["heading_path"]) if row["heading_path"] else None,
        )
        for row in rows
    ]


async def search_context(
    pool: asyncpg.Pool | SqlGateway,
    base_search: Callable[[SearchQuery], Awaitable[list[SearchResult]]],
    query: SearchContextQuery,
) -> list[SearchResult]:
    """Search with surrounding context chunks."""
    base_results = await base_search(
        SearchQuery(
            tenant_id=query.tenant_id,
            query=query.query,
            k=query.k,
        )
    )
    if is_gateway(pool):
        async with pool.transaction(str(query.tenant_id)) as reader:
            return await _expand_context(reader, base_results, query)
    async with tenant_connection(pool, str(query.tenant_id)) as conn:
        return await _expand_context(conn, base_results, query)


async def _expand_context(
    reader: Any,
    base_results: list[SearchResult],
    query: SearchContextQuery,
) -> list[SearchResult]:
    """Expand each result with surrounding context chunks, in base order."""
    expanded: list[SearchResult] = []
    for result in base_results:
        expanded.append(result)
        context = await reader.fetch(
            """
            SELECT c.chunk_id, c.text, c.doc_id, d.source, 0.0 AS score,
                   c.file_path, c.start_line, c.end_line, c.chunk_index, c.heading_path
            FROM chunks c
            JOIN documents d ON c.doc_id = d.doc_id
            WHERE c.doc_id = (SELECT doc_id FROM chunks WHERE chunk_id = $1)
              AND c.tenant_id = $2
              AND c.chunk_id != $1
              AND c.tombstoned_at IS NULL AND c.superseded_at IS NULL
              AND ABS(c.chunk_index - (
                  SELECT chunk_index FROM chunks WHERE chunk_id = $1
              )) <= $3
            ORDER BY ABS(c.chunk_index - (
                SELECT chunk_index FROM chunks WHERE chunk_id = $1
            )) ASC, c.chunk_id ASC
            LIMIT $3
            """,
            str(result.chunk_id),
            str(query.tenant_id),
            query.context_chunks,
        )
        for row in context:
            expanded.append(
                SearchResult(
                    chunk_id=row["chunk_id"],
                    text=row["text"],
                    score=0.0,
                    source=row["source"],
                    doc_id=row["doc_id"],
                    file_path=row["file_path"],
                    start_line=row["start_line"],
                    end_line=row["end_line"],
                    chunk_index=row["chunk_index"],
                    heading_path=json.loads(row["heading_path"]) if row["heading_path"] else None,
                )
            )
    return expanded
