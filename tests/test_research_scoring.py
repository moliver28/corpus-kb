"""Offline tests for research.scoring (todo 14)."""

from __future__ import annotations

import numpy as np
import pytest

from corpus_kb.research.scoring import (
    NonUnitVectorError,
    assert_unit_or_zero_rows,
    normalize_rows,
    score_code_batch,
    score_unit_views,
    score_units_against_prototypes,
    stream_scores,
)


def _unit(seed: int, dim: int = 16) -> list[float]:
    rng = np.random.default_rng(seed)
    v = rng.normal(size=dim).astype(np.float32)
    v /= np.linalg.norm(v)
    return v.tolist()


def test_normalize_rows_makes_unit_vectors_and_preserves_zero():
    rng = np.random.default_rng(4)
    base = rng.normal(size=(5, 8)).astype(np.float32)
    base[0] = 0.0
    normed = normalize_rows(base)
    for i, row in enumerate(normed):
        if i == 0:
            assert np.linalg.norm(row) == 0.0
        else:
            assert abs(np.linalg.norm(row) - 1.0) < 1e-5


def test_assert_unit_or_zero_rows_passes_normed():
    assert_unit_or_zero_rows(np.array([_unit(0, 8), [0.0] * 8]))


def test_assert_unit_or_zero_rows_rejects_non_unit():
    with pytest.raises(NonUnitVectorError):
        assert_unit_or_zero_rows(np.array([[1.0, 0.0, 0.0], [0.5, 0.5, 0.0]]))


def test_score_units_against_prototypes_high_for_near_low_for_far():
    proto = [_unit(10, 32)]
    near = [_unit(10, 32)]  # same seed -> high cosine
    far = [_unit(99, 32)]
    near_scores = score_units_against_prototypes(near, proto)
    far_scores = score_units_against_prototypes(far, proto)
    assert near_scores[0] > 0.95
    assert far_scores[0] < 0.90


def test_stream_scores_yields_all_rows():
    protos = [_unit(1, 16), _unit(2, 16)]
    vecs = [_unit(i + 10, 16) for i in range(123)]
    streamed = list(stream_scores(vecs, protos, batch_size=20))
    batched = list(score_units_against_prototypes(vecs, protos))
    assert len(streamed) == len(batched)
    np.testing.assert_allclose(streamed, batched, atol=1e-6)


def test_score_unit_views_returns_expected_shape():
    protos = {
        "answer": [_unit(1, 16)],
        "qa": [_unit(2, 16)],
        "question": [_unit(3, 16)],
    }
    s = score_unit_views(_unit(1, 16), _unit(2, 16), _unit(3, 16), protos)
    assert s.answer > 0.95
    assert s.qa > 0.95
    assert s.question > 0.95


def test_score_code_batch_empty_prototype_view_yields_zeros():
    protos = {"answer": [], "qa": [_unit(2, 16)], "question": []}
    batch = score_code_batch([_unit(1, 16)], [_unit(2, 16)], [_unit(3, 16)], protos)
    assert batch.answer[0] == 0.0
    assert batch.qa[0] > 0.95
    assert batch.question[0] == 0.0


def test_zero_vector_does_not_crash():
    proto = [_unit(1, 16)]
    scores = score_units_against_prototypes([[0.0] * 16], proto)
    assert scores[0] == 0.0
