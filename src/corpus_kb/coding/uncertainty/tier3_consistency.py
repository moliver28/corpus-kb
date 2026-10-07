"""Tier 3 self-consistency: NLI-clustered semantic entropy (todo 16, v5 §10).

N=5 same-model resamples with the prompt held FIXED — only temperature/seed
vary so the entropy stays same-distribution (Oracle A r7). Rationales are
clustered by BIDIRECTIONAL (mutual) NLI entailment — the canonical method of
Kuhn et al. ICLR 2023 (arXiv:2302.09664) / Farquhar et al. Nature 2024, via a
fixed-prompt temperature-0 NLI call on the already-required Qwen3 model
(no new deps). Transitive closure (union-find) turns pairwise mutual
entailment into clusters; semantic entropy ``SE = -sum_c (n_c/N) ln(n_c/N)``
is normalized by ``ln N`` and documented as an ORDINAL (~4-6-level) estimate.

The von Neumann variant is deliberately absent (r9: v5 §10 says "or", one
implementation suffices, unexercised flag-gated code is maintenance debt).
Tiers 4/5 are DEFERRED ENTIRELY: any tier >= 4 reference is a hard
DeferredTierError at the guard (no disabled stubs, no dead interfaces).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

N_SAMPLES = 5
MAX_SUPPORTED_TIER = 3

# NLIJudge returns True when the pair is judged mutually entailing (each
# rationale entails the other — bidirectional entailment, one judgment per
# unordered pair). N=5 samples give C(5,2)=10 pairs => 10 NLI calls per
# escalated unit (r11 cost framing).
NLIJudge = Callable[[str, str], bool]


class DeferredTierError(RuntimeError):
    """Raised when a Tier 4/5 signal path is referenced before reintroduction."""


def require_supported_tier(tier: int) -> int:
    """Hard dead-interface guard: tiers 4/5 are deferred (r9).

    Raises:
        DeferredTierError: for any tier above MAX_SUPPORTED_TIER, at
            construction/assembly time — never a silent no-op.
    """
    if tier > MAX_SUPPORTED_TIER:
        raise DeferredTierError(
            f"tier {tier} is deferred (v5 §10); supported tiers are 0-{MAX_SUPPORTED_TIER}"
        )
    return tier


def mutual_entailment_pairs(rationales: Sequence[str], nli: NLIJudge) -> list[tuple[int, int]]:
    """Judge all unordered pairs; return mutually-entailing index pairs."""
    pairs: list[tuple[int, int]] = []
    for i in range(len(rationales)):
        for j in range(i + 1, len(rationales)):
            if nli(rationales[i], rationales[j]):
                pairs.append((i, j))
    return pairs


def _union_find_clusters(n: int, pairs: Sequence[tuple[int, int]]) -> list[int]:
    """Transitive closure of mutual entailment: cluster id per sample."""
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
    roots = {find(i): idx for idx, i in enumerate(sorted({find(i) for i in range(n)}))}
    return [roots[find(i)] for i in range(n)]


def normalized_semantic_entropy(cluster_sizes: Sequence[int], n: int) -> float:
    """Semantic entropy over clusters, normalized by ln N (ordinal estimate).

    ``SE = -sum_c (n_c/N) ln(n_c/N)``, divided by ``ln N`` so the value lands
    in [0, 1]: 0.0 when every resample agrees (one cluster), 1.0 when all N
    resamples are pairwise non-entailing. Documented as an ordinal (~4-6
    level) estimate, not a calibrated probability (r9/r10 framing).
    """
    if n <= 1 or not cluster_sizes:
        return 0.0
    total = sum(cluster_sizes)
    if total != n:
        raise ValueError(f"cluster sizes sum to {total}, expected {n}")
    entropy = 0.0
    for size in cluster_sizes:
        if size <= 0:
            continue
        p = size / n
        entropy -= p * math.log(p)
    return entropy / math.log(n)


@dataclass(frozen=True)
class SelfConsistencyResult:
    """Tier-3 outcome for one escalated unit."""

    semantic_entropy: float
    n_samples: int
    n_clusters: int
    cluster_sizes: list[int]
    n_nli_calls: int
    entailment_pairs: list[tuple[int, int]]


def self_consistency(rationales: Sequence[str], nli: NLIJudge) -> SelfConsistencyResult:
    """Cluster N resamples by mutual entailment and score semantic entropy.

    Args:
        rationales: The N resampled rationale strings (same fixed prompt,
            varied temperature/seed only).
        nli: Bidirectional-entailment judge (offline fixture in tests,
            fixed-prompt temperature-0 Qwen3 call in production).

    Returns:
        SelfConsistencyResult with normalized semantic entropy and the
        cluster shape (recorded for the run manifest / audit).
    """
    n = len(rationales)
    if n == 0:
        return SelfConsistencyResult(0.0, 0, 0, [], 0, [])
    pairs = mutual_entailment_pairs(rationales, nli)
    clusters = _union_find_clusters(n, pairs)
    sizes_by_cluster: dict[int, int] = {}
    for c in clusters:
        sizes_by_cluster[c] = sizes_by_cluster.get(c, 0) + 1
    sizes = sorted(sizes_by_cluster.values(), reverse=True)
    return SelfConsistencyResult(
        semantic_entropy=normalized_semantic_entropy(sizes, n),
        n_samples=n,
        n_clusters=len(sizes),
        cluster_sizes=sizes,
        n_nli_calls=n * (n - 1) // 2,
        entailment_pairs=pairs,
    )
