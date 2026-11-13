"""The query result stages: fusion pin, post-fuse rerank, parent expansion.

Pure move of ``QueryHandler._fuse_arm_rows``, ``_post_fuse``, and
``_maybe_expand_to_parent`` as module functions (250-line rule); the class
keeps thin delegating methods so the tested call shapes stay identical.

DETERMINISM PIN REGION (zero-drift rule 9): ``fuse_arm_rows`` is the
engine-neutral RRF fusion (handlers/query_fusion.py) plus provenance
re-attach, deliberately one reviewable place — the module was the
query_handler.py pin region before the split, content unchanged. No SQL
lives here; the fusion's Python sort is pinned by
``tests/test_query_fusion.py`` and the total-order unit in
``tests/test_query_determinism.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from typing import Any, cast

from corpus_kb.domain.models import SearchResult
from corpus_kb.handlers.query_fusion import FusedRow, RowLike, rrf_fuse_legs
from corpus_kb.handlers.query_integrity import REASON_RERANK_MODE_GENERATIVE
from corpus_kb.handlers.query_result_meta import citation_and_units
from corpus_kb.rag.rerank_settings import RerankSettings
from corpus_kb.rag.reranker import RERANK_MODE_GENERATIVE_BINARY
from corpus_kb.research.retrieval_settings import RetrievalSettings
from corpus_kb.storage.search_backend import SearchOutcome
from corpus_kb.utils.tracing import (
    ATTR_CANDIDATES_IN,
    ATTR_CANDIDATES_OUT,
    ATTR_FUSED_COUNT,
    ATTR_RERANK_MODEL,
    ATTR_ROW_COUNT,
    ATTR_RRF_K,
    SPAN_EXPAND_PARENT,
    SPAN_RERANK,
    SPAN_RRF_FUSION,
    span,
)

logger = logging.getLogger(__name__)


def fuse_outcome_legs(outcomes: Sequence[SearchOutcome], k: int, rrf_k: int) -> list[SearchResult]:
    """RRF fusion + provenance re-attach over one or more backend outcomes.

    Issue #136 multi-query: one outcome per retrieved query (the primary
    first, then one per rewrite); the legs fuse in that fixed order through
    the engine-neutral N-leg port (handlers/query_fusion.py
    rrf_fuse_legs). Single-outcome runs take the same path with one pair,
    which is byte-identical to the two-leg rrf_fuse call. This function is
    the determinism pin region of the result stages; the engine arms live
    behind the SearchBackend seam.
    """
    with span(SPAN_RRF_FUSION, **{ATTR_RRF_K: rrf_k}) as current:
        # Dense leg first per outcome, so a chunk present in both legs of
        # one outcome sums dense + lexical contributions in the SQL UNION
        # ALL order and the lexical (later) leg wins the attrs on overlap.
        legs: list[Sequence[RowLike]] = []
        for outcome in outcomes:
            legs.append(cast("Sequence[RowLike]", outcome.vector_rows))
            legs.append(cast("Sequence[RowLike]", outcome.lexical_rows))
        fused_rows = rrf_fuse_legs(legs, k=k, rrf_k=rrf_k)
        current.set_attribute(ATTR_FUSED_COUNT, len(fused_rows))

    # Re-attach provenance (file_path/start_line/...) from the raw
    # vector/FTS rows — the fusion carries only the five fused columns,
    # so provenance must be joined back by chunk_id. First outcome wins.
    provenance: dict[str, dict[str, Any]] = {}
    for outcome in outcomes:
        for row in (*outcome.vector_rows, *outcome.lexical_rows):
            provenance.setdefault(str(row["chunk_id"]), dict(row))

    def _to_result(row: FusedRow) -> SearchResult:
        meta = provenance.get(str(row.chunk_id), {})
        heading = meta.get("heading_path")
        citation, units = citation_and_units(meta.get("metadata"))
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
            citation=citation,
            units=units,
        )

    fused_results = [_to_result(row) for row in fused_rows]
    # Zero-drift determinism (rule 9): fused score DESC, then chunk id
    # ASC. The Python fusion returns this order (query_fusion.rrf_fuse
    # mirrors migration 024); the sort re-asserts it so tied rows cannot
    # permute between runs.
    fused_results.sort(key=lambda r: (-r.score, r.chunk_id))
    return fused_results


def fuse_arm_rows(outcome: SearchOutcome, k: int, rrf_k: int) -> list[SearchResult]:
    """RRF fusion over one backend outcome (the pre-multi-query shape).

    Delegate to :func:`fuse_outcome_legs` with the primary outcome alone —
    one pair of legs takes the same code path, so the pin-region behavior
    (span, fusion math, provenance, total order) is unchanged."""
    return fuse_outcome_legs([outcome], k, rrf_k)


async def post_fuse(
    reranker: object | None,
    rerank_settings: RerankSettings,
    pool: object,
    retrieval_settings: RetrievalSettings,
    fused_results: list[SearchResult],
    semantic_query: str,
    k: int,
    over_retrieve: int,
    tenant_id: str,
    reasons: list[str] | None = None,
) -> list[SearchResult]:
    """Post-RRF reranking, then the additive parent-expansion hook.

    U48/P6: every return passes through :func:`maybe_expand_to_parent`
    (default OFF = identity). ``reasons`` (when given) carries the
    rerank-mode note: a generative-binary scorer's verdicts are binary
    parses, so the envelope says so instead of implying cross-encoder
    grading.
    """
    if reranker is None:
        return await maybe_expand_to_parent(pool, retrieval_settings, fused_results, tenant_id)

    if callable(getattr(reranker, "score", None)):
        # U30: rerank at most reranking.candidates of the fused
        # results — never more than were over-retrieved from the arms.
        rerank_n = min(over_retrieve, rerank_settings.candidates)
        candidates = fused_results[:rerank_n]
        cand_texts = [r.text for r in candidates]
        rerank_model = str(getattr(reranker, "model", "") or "")
        with span(
            SPAN_RERANK,
            **{ATTR_RERANK_MODEL: rerank_model, ATTR_CANDIDATES_IN: rerank_n},
        ) as current:
            score_fn = getattr(reranker, "score", None)
            raw_scores = (
                await asyncio.to_thread(score_fn, semantic_query, cand_texts) if score_fn else None
            )
            if raw_scores is None:
                return await maybe_expand_to_parent(
                    pool, retrieval_settings, fused_results, tenant_id
                )
            if reasons is not None and (
                getattr(reranker, "rerank_mode", None) == RERANK_MODE_GENERATIVE_BINARY
            ):
                reasons.append(REASON_RERANK_MODE_GENERATIVE)
            if rerank_settings.calibration == "minmax":
                lo, hi = (min(raw_scores), max(raw_scores)) if raw_scores else (0.0, 1.0)
                if hi <= lo:
                    # Degenerate span (all-equal scores — the all-
                    # not_evaluable case scores every pair at the neutral
                    # 0.5): minmax maps every score to 0.0 and score_floor
                    # would then drop ALL fused results. Fail open to the
                    # un-reranked RRF order — reranking must never break
                    # search (rag/reranker.py degrade contract).
                    return await maybe_expand_to_parent(
                        pool, retrieval_settings, fused_results, tenant_id
                    )
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
            reranked = [r for r in reranked if r.score >= rerank_settings.score_floor]
            reranked.sort(key=lambda r: r.score, reverse=True)
            top_k = reranked[:k]
            current.set_attribute(ATTR_CANDIDATES_OUT, len(top_k))
            return await maybe_expand_to_parent(pool, retrieval_settings, top_k, tenant_id)

    if callable(getattr(reranker, "rerank", None)):
        with span(
            SPAN_RERANK, **{ATTR_RERANK_MODEL: str(getattr(reranker, "model", "") or "")}
        ) as current:
            rerank_fn = getattr(reranker, "rerank", None)
            rerank_results = await rerank_fn(semantic_query, fused_results) if rerank_fn else []
            current.set_attribute(ATTR_CANDIDATES_OUT, len(rerank_results))
        return await maybe_expand_to_parent(pool, retrieval_settings, rerank_results, tenant_id)

    return await maybe_expand_to_parent(pool, retrieval_settings, fused_results, tenant_id)


async def maybe_expand_to_parent(
    pool: object,
    retrieval_settings: RetrievalSettings,
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
    if not retrieval_settings.expand_to_parent or not results:
        return results
    from corpus_kb.rag.parent_expand import ParentPool, expand_to_parent

    try:
        with span(SPAN_EXPAND_PARENT) as current:
            # asyncpg.Pool satisfies the minimal ParentPool surface at
            # runtime; the proxy reader is not a static subtype.
            expanded = await expand_to_parent(cast("ParentPool", pool), tenant_id, results)
            current.set_attribute(ATTR_ROW_COUNT, len(expanded))
            return expanded
    except Exception as exc:
        logger.warning("Parent expansion failed; returning fused results: %s", exc)
        return results
