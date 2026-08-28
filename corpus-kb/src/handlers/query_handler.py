"""Query handler - read-side queries via asyncpg + pgvector.

All queries use parameterized SQL with SET LOCAL app.current_tenant_id
for RLS enforcement. No SQLAlchemy - asyncpg only.

Usage:
    from src.handlers.query_handler import get_query_handler
    handler = get_query_handler()
    results = await handler.handle_search(SearchQuery(query="test", k=10))
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional
from uuid import UUID

import asyncpg

from src.domain.models import (
    DocumentResult,
    EntityResult,
    ListDocumentsQuery,
    ListEntitiesQuery,
    SQLQuery,
    SearchContextQuery,
    SearchQuery,
    SearchResult,
    SearchSimilarQuery,
    VerifyAnswerQuery,
    VerifyAnswerResult,
)
from src.rag.embedder import OllamaEmbedder
from src.storage.tenant_conn import tenant_connection

logger = logging.getLogger(__name__)


class QueryHandler:
    """Read-side query handler using asyncpg + pgvector.

    All methods are async and acquire a connection from the pool,
    set the tenant context via SET LOCAL, then execute parameterized SQL.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        embedder: Optional[OllamaEmbedder] = None,
        reranker: Optional[object] = None,
        self_query_parser: Optional[object] = None,
        judge: Optional[object] = None,
        config: Optional[dict[str, object]] = None,
    ) -> None:
        self._pool = pool
        self._embedder = embedder
        self._reranker = reranker
        self._self_query_parser = self_query_parser
        self._judge = judge
        cfg = config or {}
        search_cfg = cfg.get("search", {}) or {}
        self._rerank_cfg = search_cfg.get("rerank", {}) or {}
        self._self_query_enabled = bool((search_cfg.get("self_query", {}) or {}).get("enabled", False))
        self._matryoshka_enabled = bool(search_cfg.get("matryoshka_enabled", False))
        self._matryoshka_dim = int(search_cfg.get("matryoshka_dim", 1024))
        self._candidate_multiplier = int(search_cfg.get("candidate_multiplier", 8))

    async def handle_search(self, query: SearchQuery) -> list[SearchResult]:
        """Hybrid search: self-query filtering -> vector (matryoshka two-tier or
        exact) + contextual FTS -> RRF fusion -> reranking."""
        semantic_query = query.query
        dynamic_predicates: list = []
        use_self_query = query.self_query if query.self_query is not None else self._self_query_enabled
        if use_self_query and self._self_query_parser is not None:
            parsed = self._self_query_parser.parse(query.query)
            semantic_query = parsed.semantic_query
            dynamic_predicates = list(parsed.predicates)
        if query.source_type:
            from src.rag.self_query import Predicate

            dynamic_predicates.append(Predicate(target="documents.source_type", op="=", value=query.source_type))

        async with tenant_connection(self._pool, query.tenant_id) as conn:
            # NOTE: aliases below are the literal table names ("chunks"/"documents"),
            # not short aliases -- build_filter_sql() emits dynamic predicates using
            # fully-qualified column refs like "documents.source_type" (see
            # src/rag/self_query.py ALLOWED_COLUMNS), so every arm must join these
            # tables under their real names for those predicates to resolve.
            provenance_cols = "chunks.file_path, chunks.start_line, chunks.end_line, chunks.chunk_index, chunks.heading_path"
            dedup_predicate = "AND chunks.tombstoned_at IS NULL AND chunks.superseded_at IS NULL"
            # Computed here (not inside the embedder-only block below) so the
            # FTS arm can always reference it, including when self._embedder
            # is None -- it was previously scoped inside `if self._embedder:`,
            # which raised UnboundLocalError in the FTS arm for any
            # embedder-less QueryHandler with a reranker configured.
            over_retrieve_n = int(self._rerank_cfg.get("over_retrieve_n", 60)) if self._reranker else 0

            # 1. Vector search (matryoshka two-tier if enabled, else exact)
            vector_results: list[dict[str, Any]] = []
            if self._embedder:
                try:
                    vector_limit = max(query.k * 2, over_retrieve_n)
                    from src.rag.self_query import build_filter_sql

                    if self._matryoshka_enabled:
                        q4096 = self._embedder.embed(semantic_query)
                        q1024 = self._embedder.embed_matryoshka(semantic_query, self._matryoshka_dim)
                        frag, params = build_filter_sql(dynamic_predicates, start_index=6)
                        candidate_n = query.k * int(self._candidate_multiplier)
                        vector_results = await conn.fetch(
                            f"""
                            WITH cand AS (
                                SELECT cv.chunk_id, cv.vector
                                FROM chunks_vectors cv
                                JOIN chunks ON cv.chunk_id = chunks.chunk_id
                                JOIN documents ON chunks.doc_id = documents.doc_id
                                WHERE cv.tenant_id = $2 AND cv.vector_1024 IS NOT NULL
                                  {dedup_predicate} {frag}
                                ORDER BY cv.vector_1024 <=> $4::vector
                                LIMIT $5
                            )
                            SELECT chunks.chunk_id, chunks.text, chunks.doc_id, documents.source, {provenance_cols},
                                   1 - (cand.vector <=> $1::vector) AS score
                            FROM cand
                            JOIN chunks ON cand.chunk_id = chunks.chunk_id
                            JOIN documents ON chunks.doc_id = documents.doc_id
                            ORDER BY cand.vector <=> $1::vector ASC
                            LIMIT $3
                            """,
                            str(q4096), str(query.tenant_id), vector_limit, str(q1024), candidate_n, *params,
                        )
                    else:
                        query_vector = self._embedder.embed(semantic_query)
                        frag, params = build_filter_sql(dynamic_predicates, start_index=4)
                        vector_results = await conn.fetch(
                            f"""
                            SELECT chunks.chunk_id, chunks.text, chunks.doc_id, documents.source, {provenance_cols},
                                   1 - (cv.vector <=> $1::vector) AS score
                            FROM chunks_vectors cv
                            JOIN chunks ON cv.chunk_id = chunks.chunk_id
                            JOIN documents ON chunks.doc_id = documents.doc_id
                            WHERE cv.tenant_id = $2 {dedup_predicate} {frag}
                            ORDER BY cv.vector <=> $1::vector ASC
                            LIMIT $3
                            """,
                            str(query_vector), str(query.tenant_id), vector_limit, *params,
                        )
                except Exception as exc:
                    logger.warning("Vector search failed: %s", exc)

            # 2. Full-text search (contextual expression if any chunk has a blurb; coalesce is a no-op otherwise)
            from src.rag.self_query import build_filter_sql

            frag, params = build_filter_sql(dynamic_predicates, start_index=4)
            fts_results = await conn.fetch(
                f"""
                SELECT chunks.chunk_id, chunks.text, chunks.doc_id, documents.source, {provenance_cols},
                       ts_rank(to_tsvector('english', coalesce(chunks.context_blurb,'') || ' ' || chunks.text),
                               plainto_tsquery('english', $1)) AS score
                FROM chunks
                JOIN documents ON chunks.doc_id = documents.doc_id
                WHERE chunks.tenant_id = $2 {dedup_predicate} {frag}
                  AND to_tsvector('english', coalesce(chunks.context_blurb,'') || ' ' || chunks.text) @@ plainto_tsquery('english', $1)
                ORDER BY score DESC
                LIMIT $3
                """,
                semantic_query, str(query.tenant_id), max(query.k * 2, over_retrieve_n), *params,
            )

            # 3. RRF fusion (unchanged mechanism)
            rrf_k = 60
            scores: dict[str, float] = {}
            texts: dict[str, str] = {}
            sources: dict[str, str] = {}
            doc_ids: dict[str, str] = {}
            provenance: dict[str, dict[str, Any]] = {}

            for rank, row in enumerate(vector_results):
                cid = str(row["chunk_id"])
                scores[cid] = scores.get(cid, 0.0) + 1.0 / (rrf_k + rank + 1)
                texts[cid] = row["text"]
                sources[cid] = row["source"]
                doc_ids[cid] = str(row["doc_id"])
                provenance[cid] = dict(row)

            for rank, row in enumerate(fts_results):
                cid = str(row["chunk_id"])
                scores[cid] = scores.get(cid, 0.0) + 1.0 / (rrf_k + rank + 1)
                texts[cid] = row["text"]
                sources[cid] = row["source"]
                doc_ids[cid] = str(row["doc_id"])
                provenance.setdefault(cid, dict(row))

            ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

            # 4. Reranking (post-RRF, pre-truncation)
            if self._reranker is not None and ranked:
                over_retrieve_n = int(self._rerank_cfg.get("over_retrieve_n", 60))
                candidates = ranked[:over_retrieve_n]
                cand_texts = [texts[cid] for cid, _ in candidates]
                raw_scores = await asyncio.to_thread(self._reranker.score, semantic_query, cand_texts)
                if raw_scores is not None:
                    floor = float(self._rerank_cfg.get("score_floor", 0.15))
                    lo, hi = (min(raw_scores), max(raw_scores)) if raw_scores else (0.0, 1.0)
                    span = (hi - lo) or 1.0
                    calibrated = [(cid, (s - lo) / span) for (cid, _), s in zip(candidates, raw_scores)]
                    calibrated = [(cid, s) for cid, s in calibrated if s >= floor]
                    calibrated.sort(key=lambda x: (x[1], x[0]), reverse=True)
                    ranked = calibrated
            ranked = ranked[: query.k]

            return [
                SearchResult(
                    chunk_id=UUID(cid), text=texts[cid], score=score, source=sources[cid], doc_id=UUID(doc_ids[cid]),
                    file_path=provenance[cid].get("file_path"), start_line=provenance[cid].get("start_line"),
                    end_line=provenance[cid].get("end_line"), chunk_index=provenance[cid].get("chunk_index"),
                    heading_path=json.loads(provenance[cid]["heading_path"]) if provenance[cid].get("heading_path") else None,
                )
                for cid, score in ranked
            ]

    async def handle_sql_query(self, query: SQLQuery) -> list[dict[str, Any]]:
        """Execute a read-only SQL query."""
        async with tenant_connection(self._pool, query.tenant_id) as conn:
            rows = await conn.fetch(query.sql, *query.params.values())
            return [dict(row) for row in rows]

    async def handle_list_documents(
        self, query: ListDocumentsQuery
    ) -> list[DocumentResult]:
        """List documents with pagination."""
        async with tenant_connection(self._pool, query.tenant_id) as conn:
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
        async with tenant_connection(self._pool, query.tenant_id) as conn:
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
        async with tenant_connection(self._pool, query.tenant_id) as conn:
            rows = await conn.fetch(
                """
                SELECT c.chunk_id, c.text, c.doc_id, d.source,
                       c.file_path, c.start_line, c.end_line, c.chunk_index, c.heading_path,
                       1 - (cv.vector <=> (
                           SELECT vector FROM chunks_vectors WHERE chunk_id = $1
                       )) AS score
                FROM chunks_vectors cv
                JOIN chunks c ON cv.chunk_id = c.chunk_id
                JOIN documents d ON c.doc_id = d.doc_id
                WHERE cv.tenant_id = $2 AND cv.chunk_id != $1
                  AND c.tombstoned_at IS NULL AND c.superseded_at IS NULL
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
                    file_path=row["file_path"],
                    start_line=row["start_line"],
                    end_line=row["end_line"],
                    chunk_index=row["chunk_index"],
                    heading_path=json.loads(row["heading_path"]) if row["heading_path"] else None,
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
        async with tenant_connection(self._pool, query.tenant_id) as conn:
            for result in base_results:
                expanded.append(result)
                context = await conn.fetch(
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
                            file_path=row["file_path"],
                            start_line=row["start_line"],
                            end_line=row["end_line"],
                            chunk_index=row["chunk_index"],
                            heading_path=json.loads(row["heading_path"]) if row["heading_path"] else None,
                        )
                    )
        return expanded

    async def handle_verify_answer(self, query: VerifyAnswerQuery) -> VerifyAnswerResult:
        from src.domain.models import ClaimVerdict, VerifyAnswerResult

        if not query.chunk_ids or self._judge is None:
            return VerifyAnswerResult(verdicts=[], groundedness=0.0, abstained=True)
        async with tenant_connection(self._pool, query.tenant_id) as conn:
            rows = await conn.fetch(
                "SELECT chunk_id, text FROM chunks WHERE tenant_id = $1 AND chunk_id = ANY($2)",
                str(query.tenant_id), [str(cid) for cid in query.chunk_ids],
            )
        cited_texts = [row["text"] for row in rows]
        if not cited_texts:
            return VerifyAnswerResult(verdicts=[], groundedness=0.0, abstained=True)
        claims = self._judge.decompose(query.answer)
        if not claims:
            return VerifyAnswerResult(verdicts=[], groundedness=0.0, abstained=True)
        verdicts = []
        for claim in claims:
            label, confidence = self._judge.entail(claim, cited_texts)
            verdicts.append(ClaimVerdict(claim=claim, label=label, confidence=confidence, cited_chunk_ids=query.chunk_ids))
        entailed = sum(1 for v in verdicts if v.label == "entailed")
        return VerifyAnswerResult(verdicts=verdicts, groundedness=entailed / len(verdicts), abstained=False)


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
