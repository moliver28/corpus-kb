"""Active review batching: uncertainty + diversity + random stratum (U37; v8 §3 U37).

Selects the next human-review batch from the unreviewed pool:

(a) ACTIVE: lowest calibrated confidence (or highest cross-run disagreement)
    first, ordered by ascending confidence;
(b) DIVERSITY: greedy max-min (k-means++-style) selection over the stored
    embeddings, so the batch is not full of near-duplicates;
(c) RANDOM: a fixed stratified-random fraction (``review.random_fraction``,
    default 0.2) drawn seeded from the pool BEFORE the active pass. The
    random stratum is ALWAYS present and reported separately: active-learning
    gains are not uniformly robust, so only the random stratum gives
    unbiased error estimates (selection bias otherwise).

Units without a measured confidence are treated as fully confident (they
sink to the end of the active order) — the batch never fabricates
uncertainty that was not measured.

Pure numpy + stdlib; deterministic under a fixed seed; zero I/O. Output
feeds ``research/review_surface.py`` (Wave 2 wiring).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

STRATUM_ACTIVE = "active"
STRATUM_RANDOM = "random"


@dataclass(frozen=True)
class ReviewBatchItem:
    """One selected unit with its selection stratum."""

    unit_id: str
    stratum: str  # active | random
    priority: float  # calibrated confidence (lower = more review-urgent)


@dataclass(frozen=True)
class ReviewBatch:
    """The next human-review batch (U37).

    ``items`` is ordered active-first (ascending confidence), then random.
    ``random_items`` repeats the random stratum for the separate reporting
    the unbiased-error-estimate discipline requires.
    """

    items: tuple[ReviewBatchItem, ...]
    random_items: tuple[ReviewBatchItem, ...]
    batch_size: int
    random_fraction: float
    diversity_applied: bool
    n_unreviewed: int
    seed: int


def _unit_norms(embeddings: Mapping[str, Sequence[float]], ids: Sequence[str]) -> np.ndarray:
    """Row-normalized embedding matrix for the given unit ids."""
    rows = np.asarray([embeddings[uid] for uid in ids], dtype=float)
    norms = np.linalg.norm(rows, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return rows / norms


def _greedy_maxmin(
    candidates: Sequence[str], embeddings: Mapping[str, Sequence[float]], n_select: int
) -> list[str]:
    """k-means++-style greedy max-min coverage, uncertainty-ordered seeding.

    ``candidates`` must be pre-sorted by ascending confidence: the seed is
    the least-confident candidate, and distance ties break to the more
    uncertain (earlier in the sorted order), keeping uncertainty primary
    and diversity a constraint on near-duplicates.
    """
    ids = list(candidates)
    normed = _unit_norms(embeddings, ids)
    selected: list[int] = [0]
    min_dist = 1.0 - np.clip(normed @ normed[selected[0]], -1.0, 1.0)
    while len(selected) < n_select and len(selected) < len(ids):
        far = np.flatnonzero(min_dist >= min_dist.max() - 1e-12)
        pick = int(far[0])
        selected.append(pick)
        dist_to_new = 1.0 - np.clip(normed @ normed[pick], -1.0, 1.0)
        min_dist = np.minimum(min_dist, dist_to_new)
    return [ids[i] for i in selected]


def select_review_batch(
    unit_ids: Sequence[str],
    confidences: Mapping[str, float],
    embeddings: Mapping[str, Sequence[float]] | None,
    *,
    reviewed: Sequence[str] = (),
    batch_size: int = 20,
    random_fraction: float = 0.2,
    diversity: bool = True,
    seed: int = 0,
) -> ReviewBatch:
    """Select the next human-review batch (U37).

    Args:
        unit_ids: All units in scope (reviewed and unreviewed).
        confidences: Calibrated routing confidence per unit (U47 output);
            lower = more urgent for review. Missing units count as 1.0.
        embeddings: Stored embeddings per unit; None or a missing entry
            disables the diversity pass (reported in ``diversity_applied``).
        reviewed: Units already reviewed (excluded from selection).
        batch_size: Total batch size (``review.batch_size``).
        random_fraction: Fraction of the batch drawn at random
            (``review.random_fraction``); the stratum is always non-empty
            when any unit is selected.
        diversity: Greedy max-min diversity pass (``review.diversity``).
        seed: RNG seed for the random stratum (deterministic output).

    Returns:
        ReviewBatch. Duplicate-free and a strict subset of the unreviewed
        pool; when the pool is empty or batch_size <= 0 the batch is empty.
    """
    reviewed_set = set(reviewed)
    unreviewed = [u for u in unit_ids if u not in reviewed_set]
    if batch_size <= 0 or not unreviewed:
        return ReviewBatch(
            items=(),
            random_items=(),
            batch_size=batch_size,
            random_fraction=random_fraction,
            diversity_applied=False,
            n_unreviewed=len(unreviewed),
            seed=seed,
        )
    ordered = sorted(unreviewed, key=lambda u: (float(confidences.get(u, 1.0)), u))

    # Random stratum first: always present, disjoint from the active pass.
    rng = np.random.default_rng(seed)
    n_random = max(1, min(round(batch_size * random_fraction), len(ordered)))
    rand_idx = np.sort(rng.choice(len(ordered), size=n_random, replace=False))
    random_ids = [ordered[int(i)] for i in rand_idx]
    random_set = set(random_ids)

    active_pool = [u for u in ordered if u not in random_set]
    n_active = max(0, min(batch_size - n_random, len(active_pool)))
    use_diversity = (
        diversity
        and n_active > 1
        and embeddings is not None
        and all(u in embeddings for u in active_pool)
    )
    if use_diversity and embeddings is not None:
        active_ids = _greedy_maxmin(active_pool, embeddings, n_active)
    else:
        active_ids = active_pool[:n_active]

    items = tuple(
        [ReviewBatchItem(u, STRATUM_ACTIVE, float(confidences.get(u, 1.0))) for u in active_ids]
        + [ReviewBatchItem(u, STRATUM_RANDOM, float(confidences.get(u, 1.0))) for u in random_ids]
    )
    random_items = tuple(
        ReviewBatchItem(u, STRATUM_RANDOM, float(confidences.get(u, 1.0))) for u in random_ids
    )
    return ReviewBatch(
        items=items,
        random_items=random_items,
        batch_size=batch_size,
        random_fraction=random_fraction,
        diversity_applied=bool(use_diversity),
        n_unreviewed=len(unreviewed),
        seed=seed,
    )
