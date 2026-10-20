"""Query handler — read-side queries via asyncpg + pgvector.

All queries use parameterized SQL with SET LOCAL app.current_tenant_id
for RLS enforcement. No SQLAlchemy — asyncpg only.

Zero-drift split (Wave 3): the provider vector arms live in
``handlers/query_vector_arms.py`` and the small CRUD-ish handlers in
``handlers/query_crud.py``. This module keeps the QueryHandler
orchestrator, the post-fuse rerank/expand stages, and the singleton.

Usage:
    from corpus_kb.handlers.query_handler import get_query_handler
    handler = get_query_handler()
    results = await handler.handle_search(SearchQuery(query="test", k=10))
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Sequence
from typing import Any, cast

import asyncpg

from corpus_kb.config import load_config
from corpus_kb.domain.models import (
    DocumentResult,
    EntityResult,
    ListDocumentsQuery,
    ListEntitiesQuery,
    SearchContextQuery,
    SearchQuery,
    SearchResult,
    SearchSimilarQuery,
    SQLQuery,
    VerifyAnswerQuery,
    VerifyAnswerResult,
)
from corpus_kb.handlers import query_crud
from corpus_kb.handlers.query_fusion import FusedRow, RowLike, rrf_fuse
from corpus_kb.handlers.query_integrity import (
    REASON_FILTER_EMPTY,
    REASON_NO_EMBEDDER,
    REASON_RERANK_MODE_GENERATIVE,
    SearchEnvelope,
    degraded_candidate_predicate,
    finalize_envelope,
)
from corpus_kb.handlers.query_vector_arms import (
    DEDUP_PREDICATE,
    SELECT_LIST,
    ollama_vector_arm,
    pgml_vector_arm,
)
from corpus_kb.rag.embedder import OllamaEmbedder, PgmlEmbedder
from corpus_kb.rag.rerank_settings import load_rerank_settings
from corpus_kb.rag.reranker import RERANK_MODE_GENERATIVE_BINARY, build_reranker, create_reranker
from corpus_kb.research.retrieval_settings import load_retrieval_settings
from corpus_kb.research.search_settings import (
    HnswSettings,
    SqlExecutor,
    VersionReader,
    apply_hnsw_settings,
    load_hnsw_settings,
    pgvector_version,
    supports_iterative_scan,
)
from corpus_kb.utils.tracing import (
    ATTR_CANDIDATES_IN,
    ATTR_CANDIDATES_OUT,
    ATTR_EXCLUDED_COUNT,
    ATTR_FUSED_COUNT,
    ATTR_RERANK_MODEL,
    ATTR_ROW_COUNT,
    ATTR_RRF_K,
    SPAN_EXPAND_PARENT,
    SPAN_QUERY,
    SPAN_RERANK,
    SPAN_RETRIEVE_DEGRADED_PROBE,
    SPAN_RETRIEVE_FTS,
    SPAN_RETRIEVE_VECTOR,
    SPAN_RRF_FUSION,
    span,
)

logger = logging.getLogger(__name__)

# Stage 3: degraded-filter disambiguation probe. When the vector arm returns
# zero rows under an active degraded filter, this count over the SAME
# candidate universe WITHOUT the `degraded = FALSE` predicate tells a
# "filter removed everything" empty apart from a legitimately empty search
# (empty tenant, filters matching nothing). Distinct from the U21 paired
# count probe (no FILTER pair) so tests can tell the two probes apart.
_DEGRADED_CANDIDATE_COUNT_SQL = """
SELECT count(*)::float8 AS candidates
FROM chunks_vectors cv
JOIN chunks ON cv.chunk_id = chunks.chunk_id
JOIN documents ON chunks.doc_id = documents.doc_id
WHERE cv.tenant_id = $1 {dedup} {frag}
"""


class QueryHandler:
    """Read-side query handler using asyncpg + pgvector.

    All methods are async and acquire a connection from the pool,
    set the tenant context via SET LOCAL, then execute parameterized SQL.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        embedder: OllamaEmbedder | PgmlEmbedder | None = None,
        reranker: object | None = None,
        self_query_parser: object | None = None,
        judge: object | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._pool = pool
        self._embedder = embedder
        self._self_query_parser = self_query_parser
        self._judge = judge
        self._config = config or load_config()
        # U30 production wiring: ``search.rerank.enabled`` (top-N rerank of
        # the fused results with the qwen3 logprob scorer) takes precedence;
        # when the block is absent/disabled, fall back to the legacy
        # ``search.reranker`` selection so existing configs keep behaving.
        self._reranker = (
            reranker
            if reranker is not None
            else (build_reranker(self._config) or create_reranker(self._config, pool))
        )
        # Config v2: the retrieval knobs live under ``retrieval.*`` and the
        # rerank knobs under ``reranking.*`` (legacy namespaces are aliases
        # the loader already normalized).
        self._rerank_settings = load_rerank_settings(self._config)
        # U45/U21: shared retrieval knobs (``rrf_k`` feeds the fusion call
        # below; the threshold drives the exact-scan arm; the candidate
        # depths drive the live vector/FTS arm LIMITs; ``mode`` is
        # enum-validated here — a bad value fails loudly at construction).
        self._retrieval_settings = load_retrieval_settings(self._config)
        self._exclude_degraded = self._retrieval_settings.exclude_degraded
        self._self_query_enabled = bool(self._retrieval_settings.self_query.get("enabled", False))
        self._matryoshka_enabled = self._retrieval_settings.matryoshka_enabled
        self._matryoshka_dim = self._retrieval_settings.matryoshka_dim
        self._candidate_multiplier = self._retrieval_settings.candidate_multiplier
        # U20: transaction-local HNSW scan settings (retrieval.hnsw.*).
        self._hnsw_settings: HnswSettings = load_hnsw_settings(self._config)
        # U48/P6: the ingest-side hierarchy keys are owned by the pipeline;
        # the query-time expand_to_parent arm lives on retrieval settings.

    async def handle_search(self, query: SearchQuery) -> list[SearchResult]:
        """Hybrid search: self-query filtering -> vector (matryoshka two-tier or
        pgml exact) + contextual FTS -> RRF fusion -> post-RRF reranking.
        """
        return (await self.handle_search_with_meta(query)).results

    async def handle_search_with_meta(self, query: SearchQuery) -> SearchEnvelope:
        """Hybrid search with the degraded-honesty envelope attached.

        Same pipeline as handle_search; the envelope additionally reports
        ``degraded_excluded`` {count, reasons} and flips to
        ``degraded_empty`` (+ pinned remedy) when nothing was returned under
        degraded conditions.

        Default k (W3 contract): ``k=None`` (caller omitted k) resolves to
        ``reranking.return_top_k`` — an explicit caller k always wins.
        """
        with span(SPAN_QUERY):
            query = self._resolve_default_k(query)
            # Stage 3 honesty: every vector-arm impairment is recorded in
            # ``reasons`` and surfaced in the envelope's ``degraded_excluded`` —
            # the silent FTS-only fallbacks become visible.
            reasons: list[str] = []
            semantic_query, dynamic_predicates = self._resolve_predicates(query)
            over_retrieve_n = self._over_retrieve_n()

            async with self._pool.acquire() as acquired, acquired.transaction():
                conn = cast("asyncpg.Connection", acquired)
                await conn.execute(
                    "SELECT set_config('app.current_tenant_id', $1, true)",
                    str(query.tenant_id),
                )
                # U20: transaction-local HNSW settings (SET LOCAL dies with this
                # transaction — a pooled connection never leaks them). Gated on
                # the installed pgvector version: emitting `SET LOCAL hnsw.*`
                # below 0.8 aborts the query, so unknown/old versions keep the
                # pre-iterative-scan behavior (fail closed).
                extversion = await pgvector_version(cast("VersionReader", conn))
                if supports_iterative_scan(extversion):
                    await apply_hnsw_settings(cast("SqlExecutor", conn), self._hnsw_settings)

                degraded_pred = degraded_candidate_predicate(self._exclude_degraded)
                fused_results, excluded_count = await self._search_in_connection(
                    conn,
                    query,
                    semantic_query,
                    dynamic_predicates,
                    degraded_pred,
                    over_retrieve_n,
                    reasons,
                )

            # 4. Reranking (post-RRF) + envelope finalization. The rerank stage
            #    runs outside the handler's connection so a max_size=1 pool
            #    cannot deadlock on the reranker's own acquire.
            resolved_k = query.k if query.k is not None else self._rerank_settings.return_top_k
            final_results = await self._post_fuse(
                fused_results,
                semantic_query,
                resolved_k,
                over_retrieve_n,
                str(query.tenant_id),
                reasons,
            )
            return finalize_envelope(final_results, reasons, excluded_count=excluded_count)

    def _resolve_default_k(self, query: SearchQuery) -> SearchQuery:
        """W3 default-k contract: ``k=None`` (caller omitted k) becomes
        ``reranking.return_top_k``; an explicit caller k always wins."""
        if query.k is None:
            return query.model_copy(update={"k": self._rerank_settings.return_top_k})
        return query

    def _resolve_predicates(self, query: SearchQuery) -> tuple[str, list]:
        """Resolve the semantic query and its dynamic filter predicates.

        Self-query parsing (when enabled) rewrites the semantic query and
        adds parsed predicates; an explicit ``source_type`` filter always
        adds one more.
        """
        semantic_query = query.query
        dynamic_predicates: list = []
        use_self_query = (
            query.self_query if query.self_query is not None else self._self_query_enabled
        )
        if use_self_query and self._self_query_parser is not None:
            # Duck-typed test seam (stubs pass _StubParser); the cast keeps
            # that contract without widening the stored attribute's type.
            from corpus_kb.rag.self_query import ParsedQuery

            parse_fn = cast(
                "Callable[[str], ParsedQuery]", getattr(self._self_query_parser, "parse", None)
            )
            parsed = parse_fn(query.query)
            semantic_query = parsed.semantic_query
            dynamic_predicates = list(parsed.predicates)
        if query.source_type:
            from corpus_kb.rag.self_query import Predicate

            dynamic_predicates.append(
                Predicate(
                    target="documents.source_type",
                    op="=",
                    value=query.source_type,
                )
            )
        return semantic_query, dynamic_predicates

    def _over_retrieve_n(self) -> int:
        """Candidate LIMIT inflation for score-based pipeline rerankers.

        Computed up front (not inside the connection block) so both the FTS
        arm and the post-connection reranker can always reference it,
        including when self._embedder is None -- it was previously scoped
        inside `if self._embedder:`, which raised UnboundLocalError in the
        FTS arm for any embedder-less QueryHandler with a reranker configured.
        Only score-based pipeline rerankers (OllamaReranker/FakeReranker,
        the `search.rerank` system) over-retrieve candidates; the async
        `.rerank` rerankers (IdentityReranker/PgmlReranker) fuse to top-k
        directly and must not inflate the vector/FTS LIMIT.
        """
        return (
            self._rerank_settings.over_retrieve_n
            if callable(getattr(self._reranker, "score", None))
            else 0
        )

    async def _search_in_connection(
        self,
        conn: asyncpg.Connection,
        query: SearchQuery,
        semantic_query: str,
        dynamic_predicates: list,
        degraded_pred: str,
        over_retrieve_n: int,
        reasons: list[str],
    ) -> tuple[list[SearchResult], int]:
        """Arms, degraded probe, lexical arm and RRF fusion on ONE connection.

        Returns the fused results (deterministically ordered) and the count
        of candidate rows the degraded filter excluded; arm impairments are
        appended to ``reasons``. The SQL below is byte-identical to the
        pre-split inline pipeline and PINNED by file scan
        (tests/test_query_determinism.py counts this module's ORDER BY
        clauses) — do not move it to another module.
        """
        # W3 default-k contract: an omitted k (None) resolves to
        # reranking.return_top_k. handle_search_with_meta already resolved
        # it; this re-resolution keeps every entry point safe.
        k = query.k if query.k is not None else self._rerank_settings.return_top_k
        # NOTE: SELECT_LIST/DEDUP_PREDICATE (handlers/query_vector_arms.py)
        # name the literal tables "chunks"/"documents" — build_filter_sql()
        # emits dynamic predicates using fully-qualified column refs like
        # "documents.source_type", so every arm must join these tables
        # under their real names for those predicates to resolve.
        # Stage 3: candidate CTEs drop rows whose stored vector is flagged
        # degraded (all-zero backfill from migration 022). Seed subqueries
        # (handle_search_similar) are NEVER filtered — seed documents must
        # stay discoverable even when degraded.
        fts_vector = (
            "to_tsvector('english', coalesce(chunks.context_blurb,'') || ' ' || chunks.text)"
        )

        # 1. Vector search (pgml in-database, or local Ollama with optional
        #    matryoshka two-tier retrieval). Each arm returns
        #    (rows, reason|None); a reason is an arm impairment for the
        #    honesty envelope, never a raised error.
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

        # 2. Full-text search (contextual expression if any chunk has a blurb;
        #    coalesce is a no-op otherwise).
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

        # 3. RRF fusion in Python (handlers/query_fusion.py) — an exact port
        #    of corpus.rrf_fusion (migration 024): same rank constant, same
        #    1-based per-leg ranks, same float summation, same metadata rule,
        #    same total order. No fusion SQL is issued on this path anymore;
        #    research/retrieval.py keeps the SQL function for now.
        with span(SPAN_RRF_FUSION, **{ATTR_RRF_K: self._retrieval_settings.rrf_k}) as current:
            # asyncpg.Record satisfies the fusion's RowLike surface at
            # runtime; the cast mirrors the SqlExecutor/ParentPool seams
            # (asyncpg's overloaded __getitem__ defeats structural checks).
            fused_rows = rrf_fuse(
                lexical=cast("Sequence[RowLike]", fts_results),
                dense=cast("Sequence[RowLike]", vector_results),
                k=k,
                rrf_k=self._retrieval_settings.rrf_k,
            )
            current.set_attribute(ATTR_FUSED_COUNT, len(fused_rows))

        # Re-attach provenance (file_path/start_line/...) from the raw
        # vector/FTS rows — the fusion carries only the five fused columns,
        # so provenance must be joined back by chunk_id.
        provenance: dict[str, dict[str, Any]] = {}
        for row in vector_results:
            provenance.setdefault(str(row["chunk_id"]), dict(row))
        for row in fts_results:
            provenance.setdefault(str(row["chunk_id"]), dict(row))

        def _to_result(row: FusedRow) -> SearchResult:
            meta = provenance.get(str(row.chunk_id), {})
            heading = meta.get("heading_path")
            return SearchResult(
                chunk_id=row.chunk_id,
                text=row.text,
                score=float(row.score),
                source=row.source,
                doc_id=row.doc_id,
                file_path=meta.get("file_path"),
                start_line=meta.get("start_line"),
                end_line=meta.get("end_line"),
                chunk_index=meta.get("chunk_index"),
                heading_path=json.loads(heading) if heading else None,
            )

        fused_results = [_to_result(row) for row in fused_rows]
        # Zero-drift determinism (rule 9): fused score DESC, then chunk id
        # ASC. The Python fusion returns this order (query_fusion.rrf_fuse
        # mirrors migration 024); the sort re-asserts it so tied rows cannot
        # permute between runs.
        fused_results.sort(key=lambda r: (-r.score, r.chunk_id))
        return fused_results, excluded_count

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
        """Run the configured provider's vector arm and record impairments.

        Each arm returns (rows, reason|None); a reason is appended to
        ``reasons`` for the honesty envelope and the (empty) rows let the
        pipeline continue with the lexical arm alone.
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

        Stage 3 (revised): a clean vector-arm run that returned zero rows
        while the degraded filter was active is ambiguous — either the
        filter removed the whole candidate set or none ever existed. ONE
        cheap COUNT probe over the same candidate universe WITHOUT the
        degraded predicate disambiguates: the filter-empty reason (and
        the excluded count) is recorded only when the probe says rows
        were actually excluded, so legitimately empty searches return
        plain success with no remedy.
        """
        if not (self._exclude_degraded and not vector_results and not reasons):
            return 0
        from corpus_kb.rag.self_query import build_filter_sql

        probe_frag, probe_params = build_filter_sql(dynamic_predicates, start_index=2)
        try:
            # Savepoint (nested transaction): a failed probe must
            # not abort the shared transaction — the lexical arm
            # below still has to run (same arm-isolation contract
            # as the vector arms).
            async with conn.transaction():
                probe_row = await conn.fetchrow(
                    _DEGRADED_CANDIDATE_COUNT_SQL.format(dedup=DEDUP_PREDICATE, frag=probe_frag),
                    str(query.tenant_id),
                    *probe_params,
                )
        except Exception as exc:
            # Same fail-open contract as the U21 selectivity probe: a
            # broken probe must never break search — it only costs the
            # filter-empty annotation.
            logger.warning("Degraded-filter count probe failed: %s", exc)
            probe_row = None
        excluded_count = 0
        if probe_row is not None:
            raw = probe_row["candidates"]
            excluded_count = int(raw) if isinstance(raw, (int, float)) else 0
        if excluded_count > 0:
            reasons.append(REASON_FILTER_EMPTY)
        return excluded_count

    async def _post_fuse(
        self,
        fused_results: list[SearchResult],
        semantic_query: str,
        k: int,
        over_retrieve_n: int,
        tenant_id: str,
        reasons: list[str] | None = None,
    ) -> list[SearchResult]:
        """Post-RRF reranking, then the additive parent-expansion hook.

        U48/P6: every return passes through _maybe_expand_to_parent (default
        OFF = identity). ``reasons`` (when given) carries the rerank-mode
        note: a generative-binary scorer's verdicts are binary parses, so
        the envelope says so instead of implying cross-encoder grading.
        """
        if self._reranker is None:
            return await self._maybe_expand_to_parent(fused_results, tenant_id)

        if callable(getattr(self._reranker, "score", None)):
            # U30: rerank at most reranking.candidates of the fused
            # results — never more than were over-retrieved from the arms.
            rerank_n = min(over_retrieve_n, self._rerank_settings.candidates)
            candidates = fused_results[:rerank_n]
            cand_texts = [r.text for r in candidates]
            rerank_model = str(getattr(self._reranker, "model", "") or "")
            with span(
                SPAN_RERANK,
                **{ATTR_RERANK_MODEL: rerank_model, ATTR_CANDIDATES_IN: rerank_n},
            ) as current:
                score_fn = getattr(self._reranker, "score", None)
                raw_scores = (
                    await asyncio.to_thread(score_fn, semantic_query, cand_texts)
                    if score_fn
                    else None
                )
                if raw_scores is None:
                    return await self._maybe_expand_to_parent(fused_results, tenant_id)
                if reasons is not None and (
                    getattr(self._reranker, "rerank_mode", None) == RERANK_MODE_GENERATIVE_BINARY
                ):
                    reasons.append(REASON_RERANK_MODE_GENERATIVE)
                if self._rerank_settings.calibration == "minmax":
                    lo, hi = (min(raw_scores), max(raw_scores)) if raw_scores else (0.0, 1.0)
                    if hi <= lo:
                        # Degenerate span (all-equal scores — the all-
                        # not_evaluable case scores every pair at the neutral
                        # 0.5): minmax maps every score to 0.0 and score_floor
                        # would then drop ALL fused results. Fail open to the
                        # un-reranked RRF order — reranking must never break
                        # search (rag/reranker.py degrade contract).
                        return await self._maybe_expand_to_parent(fused_results, tenant_id)
                    # `width` was previously the local `span = hi - lo`;
                    # renamed because `span` now names the tracing helper.
                    width = hi - lo
                    reranked = [
                        r.model_copy(update={"score": (s - lo) / width})
                        for r, s in zip(candidates, raw_scores, strict=True)
                    ]
                else:
                    reranked = [
                        r.model_copy(update={"score": float(s)})
                        for r, s in zip(candidates, raw_scores, strict=True)
                    ]
                reranked = [r for r in reranked if r.score >= self._rerank_settings.score_floor]
                reranked.sort(key=lambda r: r.score, reverse=True)
                top_k = reranked[:k]
                current.set_attribute(ATTR_CANDIDATES_OUT, len(top_k))
                return await self._maybe_expand_to_parent(top_k, tenant_id)

        if callable(getattr(self._reranker, "rerank", None)):
            with span(
                SPAN_RERANK, **{ATTR_RERANK_MODEL: str(getattr(self._reranker, "model", "") or "")}
            ) as current:
                rerank_fn = getattr(self._reranker, "rerank", None)
                rerank_results = await rerank_fn(semantic_query, fused_results) if rerank_fn else []
                current.set_attribute(ATTR_CANDIDATES_OUT, len(rerank_results))
            return await self._maybe_expand_to_parent(rerank_results, tenant_id)

        return await self._maybe_expand_to_parent(fused_results, tenant_id)

    async def _maybe_expand_to_parent(
        self,
        results: list[SearchResult],
        tenant_id: str,
    ) -> list[SearchResult]:
        """U48/P6 additive arm: small-to-big parent expansion post-fusion.

        Gated by ``retrieval.expand_to_parent`` (config v2; the flag moved
        from ingest.hierarchy and flipped to true). The flag-off passthrough
        returns the SAME list object without touching the pool.
        Fail-open: any expansion error returns the results unchanged (same
        contract as the U21 selectivity probe).
        """
        if not self._retrieval_settings.expand_to_parent or not results:
            return results
        from corpus_kb.rag.parent_expand import ParentPool, expand_to_parent

        try:
            with span(SPAN_EXPAND_PARENT) as current:
                # asyncpg.Pool satisfies the minimal ParentPool surface at
                # runtime; the proxy reader is not a static subtype.
                expanded = await expand_to_parent(
                    cast("ParentPool", self._pool), tenant_id, results
                )
                current.set_attribute(ATTR_ROW_COUNT, len(expanded))
                return expanded
        except Exception as exc:
            logger.warning("Parent expansion failed; returning fused results: %s", exc)
            return results

    # -- Small CRUD-ish handlers (moved to handlers/query_crud.py; the
    #    delegating methods keep the public surface byte-identical). --

    async def handle_sql_query(self, query: SQLQuery) -> list[dict[str, Any]]:
        """Execute a read-only SQL query."""
        return await query_crud.sql_query(self._pool, query)

    async def handle_list_documents(self, query: ListDocumentsQuery) -> list[DocumentResult]:
        """List documents with pagination."""
        return await query_crud.list_documents(self._pool, query)

    async def handle_list_entities(self, query: ListEntitiesQuery) -> list[EntityResult]:
        """List entities, optionally filtered by type."""
        return await query_crud.list_entities(self._pool, query)

    async def handle_search_similar(self, query: SearchSimilarQuery) -> list[SearchResult]:
        """Find chunks similar to a given chunk via vector distance."""
        return await query_crud.search_similar(
            self._pool, exclude_degraded=self._exclude_degraded, query=query
        )

    async def handle_search_context(self, query: SearchContextQuery) -> list[SearchResult]:
        """Search with surrounding context chunks."""
        return await query_crud.search_context(self._pool, self.handle_search, query)

    async def handle_verify_answer(self, query: VerifyAnswerQuery) -> VerifyAnswerResult:
        """Verify an LLM-generated answer's claims against cited chunks."""
        return await query_crud.verify_answer(self._pool, judge=self._judge, query=query)


# ============================================================================
# Singleton
# ============================================================================

_query_handler: QueryHandler | None = None


def get_query_handler(pool: asyncpg.Pool | None = None) -> QueryHandler:
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
