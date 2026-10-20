"""Postgres SearchBackend: the search arms moved from QueryHandler.

Storage-portability program §3.3/§3.4: a PURE MOVE of the PG search arms
out of ``handlers/query_handler.py`` — vector-arm dispatch, degraded count
probe, lexical FTS arm. The SQL below is byte-identical to the pre-move
pipeline and PINNED by file scan (tests/test_query_determinism.py counts
this module's ORDER BY clauses). QueryHandler keeps orchestration, the
tenant GUC + HNSW transaction, the engine-neutral RRF fusion, reranking,
and the envelope. The engine modes live in query_integrity; both are
engine defaults here, so no conditional ``*_index_note`` is emitted.
"""

from __future__ import annotations

import logging
from typing import Any, cast

import asyncpg

from corpus_kb.config import load_config
from corpus_kb.domain.models import SearchQuery
from corpus_kb.handlers.query_integrity import (
    PG_LEXICAL_INDEX_MODE,
    PG_VECTOR_INDEX_MODE,
    REASON_FILTER_EMPTY,
    REASON_NO_EMBEDDER,
)
from corpus_kb.handlers.query_vector_arms import (
    DEDUP_PREDICATE,
    SELECT_LIST,
    ollama_vector_arm,
    pgml_vector_arm,
)
from corpus_kb.rag.embedder import OllamaEmbedder, PgmlEmbedder
from corpus_kb.rag.rerank_settings import load_rerank_settings
from corpus_kb.research.retrieval_settings import load_retrieval_settings
from corpus_kb.storage.search_backend import SearchOutcome, SearchRequest
from corpus_kb.utils.tracing import (
    ATTR_EXCLUDED_COUNT,
    ATTR_ROW_COUNT,
    SPAN_RETRIEVE_DEGRADED_PROBE,
    SPAN_RETRIEVE_FTS,
    SPAN_RETRIEVE_VECTOR,
    span,
)

logger = logging.getLogger(__name__)

# Stage 3 degraded-filter disambiguation probe: counts the SAME candidate
# universe WITHOUT the `degraded = FALSE` predicate (filter-empty vs empty).
_DEGRADED_CANDIDATE_COUNT_SQL = """
SELECT count(*)::float8 AS candidates
FROM chunks_vectors cv
JOIN chunks ON cv.chunk_id = chunks.chunk_id
JOIN documents ON chunks.doc_id = documents.doc_id
WHERE cv.tenant_id = $1 {dedup} {frag}
"""


class PostgresSearchBackend:
    """The Postgres search arms on the SearchBackend protocol.

    Built from the handler's config and embedder, so every knob matches
    the pre-move pipeline value for value.
    """

    def __init__(
        self,
        embedder: OllamaEmbedder | PgmlEmbedder | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._embedder = embedder
        self._config = config if config is not None else load_config()
        self._rerank_settings = load_rerank_settings(self._config)
        self._retrieval_settings = load_retrieval_settings(self._config)
        self._exclude_degraded = self._retrieval_settings.exclude_degraded
        self._matryoshka_enabled = self._retrieval_settings.matryoshka_enabled
        self._matryoshka_dim = self._retrieval_settings.matryoshka_dim
        self._candidate_multiplier = self._retrieval_settings.candidate_multiplier

    async def search(self, request: SearchRequest) -> SearchOutcome:
        """Arms + degraded probe on ONE connection; fusion stays in the handler.

        Returns the raw arm rows for the handler's RRF fusion, the probe
        count, and the engine index modes; impairments go to
        ``request.reasons``. SQL is byte-identical to the pre-move pipeline
        (file-scan pinned by tests/test_query_determinism.py).
        """
        conn, query, reasons = request.conn, request.query, request.reasons
        semantic_query = request.semantic_query
        dynamic_predicates = request.dynamic_predicates
        degraded_pred, over_retrieve_n = request.degraded_pred, request.over_retrieve_n
        # W3 default-k contract: an omitted k resolves to reranking.return_top_k.
        k = query.k if query.k is not None else self._rerank_settings.return_top_k
        # NOTE: arms join "chunks"/"documents" under their real names so
        # build_filter_sql()'s fully-qualified predicates resolve; degraded
        # candidate CTEs drop flagged rows, seed subqueries are never filtered.
        fts_vector = (
            "to_tsvector('english', coalesce(chunks.context_blurb,'') || ' ' || chunks.text)"
        )

        # 1. Vector search (pgml in-database, or local Ollama; each arm
        #    returns (rows, reason|None) — a reason is an arm impairment).
        with span(SPAN_RETRIEVE_VECTOR) as current:
            vector_results = await self._vector_arm_rows(
                conn,
                query,
                semantic_query,
                dynamic_predicates,
                degraded_pred,
                over_retrieve_n,
                reasons,
            )
            current.set_attribute(ATTR_ROW_COUNT, len(vector_results))
        with span(SPAN_RETRIEVE_DEGRADED_PROBE) as current:
            excluded_count = await self._degraded_probe(
                conn, query, vector_results, reasons, dynamic_predicates
            )
            current.set_attribute(ATTR_EXCLUDED_COUNT, excluded_count)

        # 2. Full-text search (contextual expression if any chunk has a blurb).
        from corpus_kb.rag.self_query import build_filter_sql

        frag, params = build_filter_sql(dynamic_predicates, start_index=4)
        with span(SPAN_RETRIEVE_FTS) as current:
            fts_results = await conn.fetch(
                f"""
                SELECT {SELECT_LIST},
                       ts_rank({fts_vector}, plainto_tsquery('english', $1)) AS score
                FROM chunks
                JOIN documents ON chunks.doc_id = documents.doc_id
                WHERE chunks.tenant_id = $2 {DEDUP_PREDICATE} {frag}
                  AND {fts_vector} @@ plainto_tsquery('english', $1)
                ORDER BY score DESC, chunks.chunk_id ASC
                LIMIT $3
                """,
                semantic_query,
                str(query.tenant_id),
                max(self._retrieval_settings.lexical_candidates, k * 2),
                *params,
            )
            current.set_attribute(ATTR_ROW_COUNT, len(fts_results))

        return SearchOutcome(
            vector_rows=vector_results,
            lexical_rows=fts_results,
            excluded_count=excluded_count,
            vector_index_mode=PG_VECTOR_INDEX_MODE,
            lexical_index_mode=PG_LEXICAL_INDEX_MODE,
        )

    async def _vector_arm_rows(
        self,
        conn: asyncpg.Connection,
        query: SearchQuery,
        semantic_query: str,
        dynamic_predicates: list,
        degraded_pred: str,
        over_retrieve_n: int,
        reasons: list[str],
    ) -> list[asyncpg.Record]:
        """Run the configured provider's vector arm; impairments go to reasons.

        Each arm returns (rows, reason|None); empty rows let the pipeline
        continue with the lexical arm alone.
        """
        embedding_cfg = self._config.get("embedding", {}) or {}
        provider = str(embedding_cfg.get("provider", "ollama"))
        # The caller resolved the default k; narrow it for the typed arm calls.
        resolved_k = query.k if query.k is not None else self._rerank_settings.return_top_k
        if provider == "pgml":
            model = str(embedding_cfg.get("model", "qwen3-embedding:4b"))
            vector_results, arm_reason = await pgml_vector_arm(
                conn,
                model=model,
                semantic_query=semantic_query,
                tenant_id=str(query.tenant_id),
                k=resolved_k,
                vector_candidates=self._retrieval_settings.vector_candidates,
                dynamic_predicates=dynamic_predicates,
                degraded_pred=degraded_pred,
                exact_threshold=self._retrieval_settings.exact_filter_selectivity_threshold,
            )
        elif provider == "ollama" and self._embedder:
            vector_results, arm_reason = await ollama_vector_arm(
                conn,
                embedder=cast("OllamaEmbedder", self._embedder),
                semantic_query=semantic_query,
                tenant_id=str(query.tenant_id),
                k=resolved_k,
                vector_candidates=self._retrieval_settings.vector_candidates,
                dynamic_predicates=dynamic_predicates,
                degraded_pred=degraded_pred,
                matryoshka_enabled=self._matryoshka_enabled,
                matryoshka_dim=self._matryoshka_dim,
                candidate_multiplier=self._candidate_multiplier,
                exact_threshold=self._retrieval_settings.exact_filter_selectivity_threshold,
            )
        elif provider == "ollama":
            # Provider is ollama but no embedder was wired: the vector arm
            # cannot run at all — say so instead of a silent FTS-only answer.
            vector_results, arm_reason = [], REASON_NO_EMBEDDER
        else:
            raise ValueError(
                f"Unknown embedding provider {provider!r}: expected 'pgml' or 'ollama'"
            )
        if arm_reason:
            reasons.append(arm_reason)
        return vector_results

    async def _degraded_probe(
        self,
        conn: asyncpg.Connection,
        query: SearchQuery,
        vector_results: list[asyncpg.Record],
        reasons: list[str],
        dynamic_predicates: list,
    ) -> int:
        """Count rows the degraded filter excluded (0 when no probe ran).

        A clean vector-arm run that returned zero rows under an active
        degraded filter is ambiguous: ONE cheap COUNT probe without the
        degraded predicate disambiguates (plain empty stays plain success).
        """
        if not (self._exclude_degraded and not vector_results and not reasons):
            return 0
        from corpus_kb.rag.self_query import build_filter_sql

        probe_frag, probe_params = build_filter_sql(dynamic_predicates, start_index=2)
        try:
            # Savepoint: a failed probe must not abort the shared
            # transaction — the lexical arm still has to run (arm isolation).
            async with conn.transaction():
                probe_row = await conn.fetchrow(
                    _DEGRADED_CANDIDATE_COUNT_SQL.format(dedup=DEDUP_PREDICATE, frag=probe_frag),
                    str(query.tenant_id),
                    *probe_params,
                )
        except Exception as exc:
            # Same fail-open contract as the U21 selectivity probe: a broken
            # probe must never break search — it only costs the annotation.
            logger.warning("Degraded-filter count probe failed: %s", exc)
            probe_row = None
        excluded_count = 0
        if probe_row is not None:
            raw = probe_row["candidates"]
            excluded_count = int(raw) if isinstance(raw, (int, float)) else 0
        if excluded_count > 0:
            reasons.append(REASON_FILTER_EMPTY)
        return excluded_count
