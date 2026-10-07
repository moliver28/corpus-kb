"""Offline tests for research.k_medoids / PAM prototypes (todo 14)."""

from __future__ import annotations

import numpy as np
import pytest

from corpus_kb.research.prototypes import (
    build_code_prototypes,
    build_view_prototypes,
    choose_k,
    k_medoids,
)


def _unit(seed: int, dim: int = 16) -> list[float]:
    rng = np.random.default_rng(seed)
    v = rng.normal(size=dim).astype(np.float32)
    v /= np.linalg.norm(v)
    return v.tolist()


def test_k_medoids_returns_k_unit_vectors():
    rng = np.random.default_rng(1)
    vectors = [_unit(i, 16) for i in range(20)]
    protos = k_medoids(vectors, 3, rng=rng)
    assert len(protos) == 3
    for p in protos:
        assert len(p) == 16
        norm = np.linalg.norm(p)
        assert abs(norm - 1.0) < 1e-5


def test_k_medoids_deterministic_with_same_seed():
    rng = np.random.default_rng(7)
    vectors = [_unit(i, 16) for i in range(30)]
    a = k_medoids(vectors, 4, rng=rng)
    rng = np.random.default_rng(7)
    b = k_medoids(vectors, 4, rng=rng)
    assert a == b


def test_k_medoids_k_equals_n_returns_all():
    vectors = [_unit(i, 8) for i in range(4)]
    protos = k_medoids(vectors, 4)
    assert len(protos) == 4


def test_k_medoids_rejects_invalid_k():
    vectors = [_unit(i, 8) for i in range(4)]
    with pytest.raises(ValueError):
        k_medoids(vectors, 0)
    with pytest.raises(ValueError):
        k_medoids(vectors, 5)


def test_choose_k_caps_and_never_zero_for_positives():
    assert choose_k(0) == 0
    assert choose_k(1) == 1
    assert choose_k(4) == 4
    assert choose_k(100) == 5
    assert choose_k(100, max_prototypes=3) == 3


def test_build_view_prototypes_returns_unit_norm():
    rng = np.random.default_rng(2)
    vectors = [_unit(i, 32) for i in range(12)]
    protos = build_view_prototypes(vectors, max_prototypes=4, rng=rng)
    assert 1 <= len(protos) <= 4
    for p in protos:
        assert abs(np.linalg.norm(p) - 1.0) < 1e-5


def test_build_code_prototypes_per_view():
    rng = np.random.default_rng(3)
    gold = {
        "code_a": {
            "answer": [_unit(i, 16) for i in range(10)],
            "qa": [_unit(i + 100, 16) for i in range(10)],
            "question": [_unit(i + 200, 16) for i in range(10)],
        }
    }
    protos = build_code_prototypes(gold, max_prototypes=3, rng=rng)
    assert set(protos["code_a"].keys()) == {"answer", "qa", "question"}
    for view_protos in protos["code_a"].values():
        assert 1 <= len(view_protos) <= 3
