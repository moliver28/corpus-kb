"""Query handler — read-side queries via asyncpg + pgvector.

All queries use parameterized SQL with SET LOCAL app.current_tenant_id
for RLS enforcement. No SQLAlchemy — asyncpg only.

Usage:
    from handlers.query_handler import get_query_handler
    handler = get_query_handler()
    results = await handler.handle_search(SearchQuery(query="test", k=10))
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

import asyncpg

from domain.models import (
    DocumentResult,
    EntityResult,
    ListDocumentsQuery,
    ListEntitiesQuery,
    SQLQuery,
    SearchContextQuery,
    SearchQuery,
    SearchResult,
    SearchSimilarQuery,
)
from rag.embedder import OllamaEmbedder

logger = logging.getLogger(__name__)


class QueryHandler:
    """Read-side query handler using asyncpg + pgvector.

    All methods are async and acquire a connection from the pool,
    set the tenant context via SET LOCAL, then execute parameterized SQL.
    """

    def __init__(self, pool: asyncpg.Pool, embedder: Optional[OllamaEmbedder] = None) -> None:
        self._pool = pool
        self._embedder = embedder

    async def handle_search(self, query: SearchQuery) -> list[SearchResult]:
        """Hybrid search: vector similarity + full-text search with RRF fusion."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)", str(query.tenant_id)
            )

            # 1. Vector search (if embedder available)
            vector_results: list[dict[str, Any]] = []
            if self._embedder:
                try:
                    query_vector = self._embedder.embed(query.query)
                    vector_results = await conn.fetch(
                        """
                        SELECT c.chunk_id, c.text, c.doc_id, d.source,
                               1 - (cv.vector <=> $1::vector) AS score
                        FROM chunks_vectors cv
                        JOIN chunks c ON cv.chunk_id = c.chunk_id
                        JOIN documents d ON c.doc_id = d.doc_id
                        WHERE cv.tenant_id = $2
                        ORDER BY cv.vector <=> $1::vector ASC
                        LIMIT $3
                        """,
                        str(query_vector),
                        str(query.tenant_id),
                        query.k * 2,
                    )
                except Exception as exc:
                    logger.warning("Vector search failed: %s", exc)

            # 2. Full-text search
            fts_results = await conn.fetch(
                """
                SELECT c.chunk_id, c.text, c.doc_id, d.source,
                       ts_rank(to_tsvector('english', c.text), plainto_tsquery('english', $1)) AS score
                FROM chunks c
                JOIN documents d ON c.doc_id = d.doc_id
                WHERE c.tenant_id = $2
                  AND to_tsvector('english', c.text) @@ plainto_tsquery('english', $1)
                ORDER BY score DESC
                LIMIT $3
                """,
                query.query,
                str(query.tenant_id),
                query.k * 2,
            )

            # 3. RRF fusion
            rrf_k = 60
            scores: dict[str, float] = {}
            texts: dict[str, str] = {}
            sources: dict[str, str] = {}
            doc_ids: dict[str, str] = {}

            for rank, row in enumerate(vector_results):
                cid = str(row["chunk_id"])
                scores[cid] = scores.get(cid, 0.0) + 1.0 / (rrf_k + rank + 1)
                texts[cid] = row["text"]
                sources[cid] = row["source"]
                doc_ids[cid] = str(row["doc_id"])

            for rank, row in enumerate(fts_results):
                cid = str(row["chunk_id"])
                scores[cid] = scores.get(cid, 0.0) + 1.0 / (rrf_k + rank + 1)
                texts[cid] = row["text"]
                sources[cid] = row["source"]
                doc_ids[cid] = str(row["doc_id"])

            # Sort by RRF score, take top k
            ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[: query.k]

            return [
                SearchResult(
                    chunk_id=UUID(cid),
                    text=texts[cid],
                    score=score,
                    source=sources[cid],
                    doc_id=UUID(doc_ids[cid]),
                )
                for cid, score in ranked
            ]

    async def handle_sql_query(self, query: SQLQuery) -> list[dict[str, Any]]:
        """Execute a read-only SQL query."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)", str(query.tenant_id)
            )
            rows = await conn.fetch(query.sql, *query.params.values())
            return [dict(row) for row in rows]

    async def handle_list_documents(
        self, query: ListDocumentsQuery
    ) -> list[DocumentResult]:
        """List documents with pagination."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)", str(query.tenant_id)
            )
            rows = await conn.fetch(
                """
                SELECT doc_id, source, source_type, chunk_count, created_at
                FROM documents
                WHERE tenant_id = $1
                ORDER BY created_at DESC
                LIMIT $2 OFFSET $3
                """,
                str(query.tenant_id),
                query.limit,
                query.offset,
            )
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

    async def handle_list_entities(
        self, query: ListEntitiesQuery
    ) -> list[EntityResult]:
        """List entities, optionally filtered by type."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)", str(query.tenant_id)
            )
            if query.entity_type:
                rows = await conn.fetch(
                    """
                    SELECT entity_id, name, entity_type, metadata
                    FROM entities
                    WHERE tenant_id = $1 AND entity_type = $2
                    ORDER BY name ASC
                    LIMIT $3
                    """,
                    str(query.tenant_id),
                    query.entity_type,
                    query.limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT entity_id, name, entity_type, metadata
                    FROM entities
                    WHERE tenant_id = $1
                    ORDER BY name ASC
                    LIMIT $2
                    """,
                    str(query.tenant_id),
                    query.limit,
                )
            return [
                EntityResult(
                    entity_id=row["entity_id"],
                    name=row["name"],
                    entity_type=row["entity_type"],
                    metadata=dict(row["metadata"]) if row["metadata"] else {},
                )
                for row in rows
            ]

    async def handle_search_similar(
        self, query: SearchSimilarQuery
    ) -> list[SearchResult]:
        """Find chunks similar to a given chunk via vector distance."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)", str(query.tenant_id)
            )
            rows = await conn.fetch(
                """
                SELECT c.chunk_id, c.text, c.doc_id, d.source,
                       1 - (cv.vector <=> (
                           SELECT vector FROM chunks_vectors WHERE chunk_id = $1
                       )) AS score
                FROM chunks_vectors cv
                JOIN chunks c ON cv.chunk_id = c.chunk_id
                JOIN documents d ON c.doc_id = d.doc_id
                WHERE cv.tenant_id = $2 AND cv.chunk_id != $1
                ORDER BY cv.vector <=> (
                    SELECT vector FROM chunks_vectors WHERE chunk_id = $1
                ) ASC
                LIMIT $3
                """,
                str(query.chunk_id),
                str(query.tenant_id),
                query.k,
            )
            return [
                SearchResult(
                    chunk_id=row["chunk_id"],
                    text=row["text"],
                    score=row["score"],
                    source=row["source"],
                    doc_id=row["doc_id"],
                )
                for row in rows
            ]

    async def handle_search_context(
        self, query: SearchContextQuery
    ) -> list[SearchResult]:
        """Search with surrounding context chunks."""
        base_results = await self.handle_search(
            SearchQuery(
                tenant_id=query.tenant_id,
                query=query.query,
                k=query.k,
            )
        )
        # Expand each result with surrounding chunks
        expanded: list[SearchResult] = []
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)", str(query.tenant_id)
            )
            for result in base_results:
                expanded.append(result)
                context = await conn.fetch(
                    """
                    SELECT c.chunk_id, c.text, c.doc_id, d.source, 0.0 AS score
                    FROM chunks c
                    JOIN documents d ON c.doc_id = d.doc_id
                    WHERE c.doc_id = (SELECT doc_id FROM chunks WHERE chunk_id = $1)
                      AND c.tenant_id = $2
                      AND c.chunk_id != $1
                      AND ABS(c.chunk_index - (
                          SELECT chunk_index FROM chunks WHERE chunk_id = $1
                      )) <= $3
                    ORDER BY ABS(c.chunk_index - (
                        SELECT chunk_index FROM chunks WHERE chunk_id = $1
                    )) ASC
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
                        )
                    )
        return expanded


# ============================================================================
# Singleton
# ============================================================================

_query_handler: Optional[QueryHandler] = None


def get_query_handler(pool: Optional[asyncpg.Pool] = None) -> QueryHandler:
    """Get or create the singleton QueryHandler."""
    global _query_handler
    if _query_handler is None:
        if pool is None:
            raise RuntimeError("QueryHandler requires an asyncpg pool")
        _query_handler = QueryHandler(pool)
    return _query_handler


def set_query_handler(handler: QueryHandler) -> None:
    """Set the singleton (for server wiring)."""
    global _query_handler
    _query_handler = handler


def reset_query_handler() -> None:
    """Reset the singleton (for testing)."""
    global _query_handler
    _query_handler = None