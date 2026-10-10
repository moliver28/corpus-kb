"""Determinism/shape tests for active review batching (U37)."""

from __future__ import annotations

import numpy as np

from corpus_kb.research.review_batch import (
    STRATUM_ACTIVE,
    STRATUM_RANDOM,
    select_review_batch,
)


def _pool(n: int, seed: int = 0) -> tuple[list[str], dict[str, float], dict[str, list[float]]]:
    rng = np.random.default_rng(seed)
    ids = [f"u{i}" for i in range(n)]
    confidences = {uid: float(rng.random()) for uid in ids}
    embeddings = {uid: [float(v) for v in rng.normal(size=8)] for uid in ids}
    return ids, confidences, embeddings


def test_batch_is_strict_subset_without_duplicates() -> None:
    ids, confidences, embeddings = _pool(100)
    reviewed = ids[:10]
    batch = select_review_batch(
        ids, confidences, embeddings, reviewed=reviewed, batch_size=20, seed=3
    )
    selected = [item.unit_id for item in batch.items]
    assert len(selected) == len(set(selected))  # no duplicates
    assert set(selected) <= set(ids) - set(reviewed)  # strict unreviewed subset
    assert batch.n_unreviewed == 90


def test_random_stratum_always_present_and_sized() -> None:
    ids, confidences, embeddings = _pool(100)
    batch = select_review_batch(ids, confidences, embeddings, batch_size=20, seed=1)
    random_items = [i for i in batch.items if i.stratum == STRATUM_RANDOM]
    assert len(random_items) == 4  # round(20 * 0.2)
    assert len(batch.random_items) == 4
    active = [i for i in batch.items if i.stratum == STRATUM_ACTIVE]
    assert len(active) == 16


def test_active_stratum_uncertainty_first() -> None:
    ids, confidences, embeddings = _pool(60, seed=5)
    ranked = sorted(ids, key=lambda u: confidences[u])
    # With diversity OFF the active stratum is pure ascending confidence
    # (minus the one random-stratum unit the stratum floor reserves).
    plain = select_review_batch(
        ids,
        confidences,
        embeddings,
        batch_size=10,
        random_fraction=0.0,
        diversity=False,
        seed=0,
    )
    active_ids = [i.unit_id for i in plain.items if i.stratum == STRATUM_ACTIVE]
    assert set(active_ids) <= set(ranked[:10])
    # With diversity ON the SEED is still the most uncertain unit.
    diverse = select_review_batch(
        ids,
        confidences,
        embeddings,
        batch_size=10,
        random_fraction=0.0,
        diversity=True,
        seed=0,
    )
    first_active = next(i.unit_id for i in diverse.items if i.stratum == STRATUM_ACTIVE)
    assert first_active == ranked[0]


def test_diversity_breaks_duplicate_clusters() -> None:
    # Two tight clusters; the most uncertain units all sit in cluster A.
    # Without diversity the batch would be all-A; with it, B is represented.
    rng = np.random.default_rng(7)
    center_a = np.zeros(6)
    center_b = np.ones(6) * 5.0
    ids: list[str] = []
    confidences: dict[str, float] = {}
    embeddings: dict[str, list[float]] = {}
    for i in range(20):
        uid = f"a{i}"
        ids.append(uid)
        confidences[uid] = 0.01 * i  # A units are the most uncertain
        embeddings[uid] = list(center_a + rng.normal(0, 0.01, 6))
    for i in range(20):
        uid = f"b{i}"
        ids.append(uid)
        confidences[uid] = 0.9
        embeddings[uid] = list(center_b + rng.normal(0, 0.01, 6))
    batch = select_review_batch(
        ids, confidences, embeddings, batch_size=10, random_fraction=0.0, seed=0
    )
    active_ids = [i.unit_id for i in batch.items if i.stratum == STRATUM_ACTIVE]
    assert any(uid.startswith("b") for uid in active_ids)
    assert batch.diversity_applied


def test_missing_embeddings_disables_diversity() -> None:
    ids, confidences, _ = _pool(50)
    batch = select_review_batch(ids, confidences, None, batch_size=10, random_fraction=0.0, seed=0)
    assert not batch.diversity_applied
    partial = {u: [0.0, 1.0] for u in ids[:10]}
    batch2 = select_review_batch(
        ids, confidences, partial, batch_size=10, random_fraction=0.0, seed=0
    )
    assert not batch2.diversity_applied


def test_deterministic_given_seed() -> None:
    ids, confidences, embeddings = _pool(80, seed=9)
    a = select_review_batch(ids, confidences, embeddings, batch_size=15, seed=42)
    b = select_review_batch(ids, confidences, embeddings, batch_size=15, seed=42)
    assert a == b


def test_different_seed_changes_random_stratum() -> None:
    ids, confidences, embeddings = _pool(200, seed=2)
    a = select_review_batch(ids, confidences, embeddings, batch_size=10, seed=1)
    b = select_review_batch(ids, confidences, embeddings, batch_size=10, seed=2)
    random_a = {i.unit_id for i in a.random_items}
    random_b = {i.unit_id for i in b.random_items}
    assert random_a != random_b


def test_empty_pool_and_nonpositive_batch() -> None:
    ids, confidences, embeddings = _pool(5)
    empty = select_review_batch([], confidences, embeddings, batch_size=10, seed=0)
    assert empty.items == () and empty.n_unreviewed == 0
    reviewed_all = select_review_batch(
        ids, confidences, embeddings, reviewed=ids, batch_size=10, seed=0
    )
    assert reviewed_all.items == () and reviewed_all.n_unreviewed == 0
    zero = select_review_batch(ids, confidences, embeddings, batch_size=0, seed=0)
    assert zero.items == ()


def test_batch_never_exceeds_pool() -> None:
    ids, confidences, embeddings = _pool(7)
    batch = select_review_batch(ids, confidences, embeddings, batch_size=50, seed=0)
    selected = [item.unit_id for item in batch.items]
    assert set(selected) == set(ids)  # everything selected exactly once
    assert batch.random_items  # random stratum still present
