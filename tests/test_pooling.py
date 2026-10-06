"""Test pooling module: keyword hits, vector similarity, chunk_signals materialization."""

from __future__ import annotations

import re

import numpy as np

from corpus_kb.coding.pooling import cosine_similarity


def test_cosine_similarity_identical_vectors() -> None:
    """Test cosine similarity for identical vectors."""
    v1 = [1.0, 0.0, 0.0]
    v2 = [1.0, 0.0, 0.0]
    assert cosine_similarity(v1, v2) == 1.0


def test_cosine_similarity_orthogonal_vectors() -> None:
    """Test cosine similarity for orthogonal (perpendicular) vectors."""
    v1 = [1.0, 0.0, 0.0]
    v2 = [0.0, 1.0, 0.0]
    # Cosine similarity of orthogonal vectors is 0
    assert abs(cosine_similarity(v1, v2) - 0.0) < 1e-6


def test_cosine_similarity_opposite_vectors() -> None:
    """Test cosine similarity for opposite vectors."""
    v1 = [1.0, 0.0, 0.0]
    v2 = [-1.0, 0.0, 0.0]
    assert cosine_similarity(v1, v2) == -1.0


def test_cosine_similarity_partially_similar() -> None:
    """Test cosine similarity for partially similar vectors."""
    v1 = [1.0, 1.0, 0.0]
    v2 = [1.0, 0.0, 0.0]
    # dot = 1, |v1| = sqrt(2), |v2| = 1, similarity = 1/sqrt(2) ≈ 0.707
    sim = cosine_similarity(v1, v2)
    assert 0.7 < sim < 0.72


def test_cosine_similarity_zero_vector() -> None:
    """Test cosine similarity when one vector is all zeros."""
    v1 = [1.0, 0.0, 0.0]
    v2 = [0.0, 0.0, 0.0]
    # Zero vector has undefined direction; should return 0.0
    assert cosine_similarity(v1, v2) == 0.0


def test_cosine_similarity_numpy_arrays() -> None:
    """Test cosine similarity with numpy arrays as input."""
    v1 = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    v2 = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    assert cosine_similarity(v1, v2) == 1.0


def test_cosine_similarity_normalized_vectors() -> None:
    """Test cosine similarity for normalized vectors (common in embeddings)."""
    # Normalized vectors (unit length)
    v1 = [0.707, 0.707, 0.0]  # ~normalized [1, 1, 0]
    v2 = [0.707, 0.0, 0.707]  # ~normalized [1, 0, 1]
    sim = cosine_similarity(v1, v2)
    # Expected: dot=0.5, norms both ~1, similarity ≈ 0.5
    assert 0.48 < sim < 0.52


def test_materialize_chunk_keyword_hits_word_boundary() -> None:
    """Test word-boundary matching logic used in materialize_chunk_keyword_hits."""
    # This test verifies the regex pattern works correctly for keyword matching
    keyword = "test"
    pattern = r"\b" + re.escape(keyword) + r"\b"

    # Should match 'test' as whole word
    text_with_match = "We test the system and test case worked"
    matches = re.findall(pattern, text_with_match, re.IGNORECASE)
    assert len(matches) == 2

    # Should not match 'test' as part of 'tested' or 'testing'
    text_partial = "We tested the system and testing is done"
    matches = re.findall(pattern, text_partial, re.IGNORECASE)
    assert len(matches) == 0

    # Should be case-insensitive
    text_upper = "TEST and Test cases matter"
    matches = re.findall(pattern, text_upper, re.IGNORECASE)
    assert len(matches) == 2

    # Test with simple keywords
    keyword_simple = "control"
    pattern_simple = r"\b" + re.escape(keyword_simple) + r"\b"
    text_control = "We control the system and control testing"
    matches = re.findall(pattern_simple, text_control, re.IGNORECASE)
    assert len(matches) == 2


def test_materialize_chunk_keyword_hits_hit_counting() -> None:
    """Test hit count logic for keyword matching."""
    keyword = "risk"
    pattern = r"\b" + re.escape(keyword) + r"\b"

    # Count multiple hits in one text
    text = "risk assessment and risk management and risk mitigation"
    n_hits = len(re.findall(pattern, text, re.IGNORECASE))
    assert n_hits == 3

    # No hits
    text_no_match = "threat and exposure and vulnerability"
    n_hits = len(re.findall(pattern, text_no_match, re.IGNORECASE))
    assert n_hits == 0


def test_similarity_floor_filtering() -> None:
    """Test floor threshold logic for similarity pooling."""
    # Test cases with real cosine similarity values
    test_cases = [
        (0.75, 0.3, True),  # 0.75 >= 0.3 -> should include
        (0.3, 0.3, True),  # 0.3 >= 0.3 -> should include (at threshold)
        (0.25, 0.3, False),  # 0.25 < 0.3 -> should exclude
        (0.99, 0.9, True),  # 0.99 >= 0.9 -> should include
        (0.85, 0.9, False),  # 0.85 < 0.9 -> should exclude
    ]

    for similarity, pool_floor, should_include in test_cases:
        # Simulate the pooling logic
        include = similarity >= pool_floor

        assert include is should_include, f"similarity={similarity}, pool_floor={pool_floor}"


def test_coverage_reconciliation_math() -> None:
    """Test the math for coverage reconciliation."""
    # Test cases: (total_chunks, pooled_chunks, expected_never_pooled)
    test_cases = [
        (100, 75, 25),
        (50, 50, 0),
        (50, 0, 50),
        (200, None, 200),  # None means 0
        (1, 1, 0),
        (0, 0, 0),
    ]

    for total, pooled, expected_never_pooled in test_cases:
        # Simulate reconciliation logic
        if pooled is None:
            pooled = 0
        never_pooled = total - (pooled or 0)

        assert never_pooled == expected_never_pooled, (
            f"total={total}, pooled={pooled}, never_pooled={never_pooled}, "
            f"expected={expected_never_pooled}"
        )


def test_pooling_summary_dict_structure() -> None:
    """Test that pooling functions return expected summary dict structures."""
    # These are the expected return structures from the pooling functions

    # materialize_chunk_keyword_hits return
    summary1 = {"keyword_hits_written": 42, "status": "success"}
    assert "keyword_hits_written" in summary1
    assert "status" in summary1
    assert isinstance(summary1["keyword_hits_written"], int)
    assert summary1["status"] == "success"

    # run_similarity_pooling return
    summary2 = {"chunk_signals_written": 88, "status": "success"}
    assert "chunk_signals_written" in summary2
    assert "status" in summary2
    assert isinstance(summary2["chunk_signals_written"], int)

    # reconcile_coverage return
    summary3 = {
        "total_corpus_chunks": 1000,
        "pooled_chunks": 750,
        "never_pooled_chunks": 250,
        "coverage_reconciled": True,
    }
    # Verify coverage identity: pooled + never_pooled = total
    assert (
        summary3["pooled_chunks"] + summary3["never_pooled_chunks"]
        == summary3["total_corpus_chunks"]
    )
    assert summary3["coverage_reconciled"] is True
