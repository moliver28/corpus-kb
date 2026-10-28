"""Recipe overrides for the calibration sweep: validation and config merge."""

from __future__ import annotations

import pytest

from corpus_kb.errors import ValidationError
from corpus_kb.eval.recipes import (
    ALLOWED_OVERRIDES,
    MAX_CANDIDATES,
    apply_overrides,
    validate_candidates,
)


def test_allowed_overrides_are_the_query_time_knobs() -> None:
    assert (
        frozenset(
            {
                "retrieval.rrf_k",
                "retrieval.vector_candidates",
                "retrieval.lexical_candidates",
                "reranking.enabled",
                "reranking.over_retrieve_n",
                "reranking.score_floor",
            }
        )
        == ALLOWED_OVERRIDES
    )


def test_apply_overrides_sets_dot_paths_on_a_copy() -> None:
    base: dict[str, object] = {
        "retrieval": {"rrf_k": 60, "mode": "hybrid_rrf"},
        "reranking": {"enabled": True},
    }
    merged = apply_overrides(base, {"retrieval.rrf_k": 10, "reranking.enabled": False})
    retrieval = merged["retrieval"]
    reranking = merged["reranking"]
    assert isinstance(retrieval, dict) and isinstance(reranking, dict)
    assert retrieval["rrf_k"] == 10
    assert retrieval["mode"] == "hybrid_rrf"
    assert reranking["enabled"] is False
    # The base config is never mutated.
    base_retrieval = base["retrieval"]
    assert isinstance(base_retrieval, dict) and base_retrieval["rrf_k"] == 60


def test_apply_overrides_creates_missing_blocks() -> None:
    merged = apply_overrides({}, {"reranking.score_floor": 0.2})
    reranking = merged["reranking"]
    assert isinstance(reranking, dict) and reranking["score_floor"] == 0.2


def test_unknown_override_key_is_refused() -> None:
    with pytest.raises(ValidationError, match=r"ingestion\.default"):
        apply_overrides({}, {"ingestion.default": "markdown_sections"})


def test_validate_candidates_caps_the_family() -> None:
    too_many = {f"c{index}": {} for index in range(MAX_CANDIDATES + 1)}
    with pytest.raises(ValidationError, match="8"):
        validate_candidates(too_many)


def test_validate_candidates_rejects_unknown_recipe_keys() -> None:
    with pytest.raises(ValidationError, match=r"ingestion\.strategy"):
        validate_candidates({"fast": {"ingestion.strategy": "markdown_sections"}})


def test_validate_candidates_accepts_the_documented_recipes() -> None:
    validate_candidates(
        {
            "tight": {"retrieval.rrf_k": 10},
            "wide": {"retrieval.vector_candidates": 400, "reranking.enabled": False},
        }
    )
