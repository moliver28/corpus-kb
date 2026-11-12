"""The multi-query fan-out stage (issue #136, 250-line split seam).

One user query becomes (variants + 1) retrieved query pairs: the primary
outcome is the caller's existing ``arm_outcome`` run; this module rewrites
the semantic query, runs one additional ``arm_outcome`` per rewrite on the
same backend, and hands the extra outcomes to the fusion stage. No fusion
math lives here — the legs merge in :func:`query_stages.fuse_outcome_legs`
through the N-leg RRF port.

Determinism (zero-drift rule 9): the rewriter is a pure function of
(query, model, seed) with a SHA256-keyed cache (handlers/query_rewrites.py);
the leg order is the rewrites' emitted order, fixed; the fusion output is
the migration-024 total order (fscore DESC, chunk id ASC).

Honest degradation: rewriter down, timeout, or zero usable rewrites means
the single-query path with the ``multi-query-rewriter-unavailable`` note in
``degraded_excluded.reasons``. The extra legs reuse the shared ``reasons``
list, so per-leg arm impairments stay visible too.
"""

from __future__ import annotations

import asyncio
import logging

import asyncpg

from corpus_kb.handlers.query_acquire import ArmRun, arm_outcome
from corpus_kb.handlers.query_integrity import REASON_MULTI_QUERY_UNAVAILABLE
from corpus_kb.handlers.query_rewrites import QueryRewriter
from corpus_kb.research.multi_query_settings import MultiQuerySettings
from corpus_kb.research.search_settings import HnswSettings
from corpus_kb.storage.gateway import SqlGateway
from corpus_kb.storage.search_backend import SearchBackend, SearchOutcome

logger = logging.getLogger(__name__)


def build_rewriter(settings: MultiQuerySettings) -> QueryRewriter | None:
    """The handler's rewriter: built only when the flag is on (issue #136).

    Off, no client exists and the pipeline takes the single-query path
    untouched; on, one instance owns the SHA256-keyed rewrite cache for
    the handler's lifetime.
    """
    if not settings.enabled:
        return None
    return QueryRewriter(
        model=settings.model,
        base_url=settings.base_url,
        timeout_seconds=settings.timeout_seconds,
        variants=settings.variants,
    )


async def rewrite_legs(
    pool: asyncpg.Pool | SqlGateway,
    search_backend: SearchBackend,
    run: ArmRun,
    hnsw_settings: HnswSettings,
    rewriter: QueryRewriter | None,
    settings: MultiQuerySettings,
) -> tuple[list[str], list[SearchOutcome]]:
    """The rewrite legs: rewrites plus one backend outcome per rewrite.

    Returns (rewrites, extra_outcomes) in fusion order. Empty tuples mean
    single-query: the flag is off, no rewriter is wired, or the rewriter
    degraded (then the honest note rides ``run.reasons``). The blocking
    LLM call runs in a thread — the reranker's ``asyncio.to_thread``
    contract — bounded by the rewriter's client timeout.
    """
    if not settings.enabled or rewriter is None:
        return [], []
    rewrites = await asyncio.to_thread(rewriter.rewrites, run.semantic_query)
    if not rewrites:
        run.reasons.append(REASON_MULTI_QUERY_UNAVAILABLE)
        return [], []
    extra_outcomes: list[SearchOutcome] = []
    for rewrite in rewrites:
        # Same query shape per leg: the caller's k, predicates, degraded
        # policy, and over-retrieve width apply unchanged; only the query
        # text differs. The shared reasons list records leg impairments.
        leg_run = ArmRun(
            query=run.query,
            semantic_query=rewrite,
            dynamic_predicates=run.dynamic_predicates,
            degraded_pred=run.degraded_pred,
            over_retrieve=run.over_retrieve,
            reasons=run.reasons,
        )
        extra_outcomes.append(await arm_outcome(pool, search_backend, leg_run, hnsw_settings))
    return rewrites, extra_outcomes
