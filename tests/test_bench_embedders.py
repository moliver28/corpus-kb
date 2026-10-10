"""U31 bench script tests: profiles, fixture-label validity, ranking, gating.

The embed loop against live Ollama is operator-run; these tests pin the
deterministic structure: versioned profiles, doctor mismatch notes,
exact-rank helpers, the zero-vector not_evaluable contract, and the output
schema of evaluate_profile against a canned deterministic embedder.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "bench_embedders.py"

_spec = importlib.util.spec_from_file_location("bench_embedders_test", SCRIPT)
assert _spec is not None and _spec.loader is not None
be = importlib.util.module_from_spec(_spec)
sys.modules["bench_embedders_test"] = be
_spec.loader.exec_module(be)


def test_profiles_are_versioned_and_dimension_consistent() -> None:
    names = [p.name for p in be.CURRENT_PROFILES]
    assert len(names) == len(set(names))
    for profile in be.CURRENT_PROFILES:
        assert profile.dimensions > 0
        assert profile.version >= 1
        assert profile.model
    assert be.profile_for_model("nomic-embed-text").dimensions == 768
    assert be.profile_for_model("qwen3-embedding:4b").dimensions == 2560
    assert be.profile_for_model("qwen3-embedding:8b-q8_0").dimensions == 4096
    with pytest.raises(ValueError, match="no EmbeddingProfile"):
        be.profile_for_model("not-a-model")


def test_doctor_mismatch_note() -> None:
    profile = be.CURRENT_PROFILES[0]
    assert be.doctor_mismatch_note(profile, profile.model, profile.dimensions) is None
    note = be.doctor_mismatch_note(profile, "other-model", 768)
    assert note is not None
    assert "NEW versioned index generation" in note
    note_dims = be.doctor_mismatch_note(profile, profile.model, 1536)
    assert note_dims is not None and "1536" in note_dims


def test_fixture_labels_are_wellformed() -> None:
    for query, relevant in be.FIXTURE_QUERIES:
        assert query
        assert relevant
        for doc_id in relevant:
            assert doc_id in be.FIXTURE_DOCS, f"unknown label doc {doc_id}"
    assert len(be.FIXTURE_QUERIES) >= 3


def test_rank_ids_orders_by_cosine() -> None:
    matrix = np.asarray([[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]], dtype=np.float32)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
    top = be.rank_ids(matrix, np.asarray([1.0, 0.0], dtype=np.float32), 2)
    assert top[0] == 0


def test_evaluate_profile_reports_not_evaluable_on_zero_vectors() -> None:
    def zero_embed(texts: list[str]) -> list[list[float]]:
        return [[0.0] * 8 for _ in texts]

    report = be.evaluate_profile(
        zero_embed,
        be.FIXTURE_DOCS,
        be.FIXTURE_QUERIES,
        be.CURRENT_PROFILES[0],
        k=2,
    )
    assert report["status"] == "not_evaluable"
    assert "zero vectors" in str(report["reason"])
    assert "recall_at_k" not in report  # no fake metrics alongside


def test_evaluate_profile_output_schema_with_deterministic_embedder() -> None:
    def fake_embed(texts: list[str]) -> list[list[float]]:
        import hashlib

        vectors = []
        for text in texts:
            seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)
            rng = np.random.default_rng(seed)
            vectors.append(rng.standard_normal(16).tolist())
        return vectors

    profile = be.EmbeddingProfile(name="fake", model="fake", dimensions=16)
    report = be.evaluate_profile(
        fake_embed,
        be.FIXTURE_DOCS,
        be.FIXTURE_QUERIES,
        profile,
        k=2,
    )
    assert report["status"] == "ok"
    for key in ("recall_at_k", "mrr", "p50_embed_ms", "p95_embed_ms", "n_queries", "k"):
        assert key in report
    assert report["n_queries"] == len(be.FIXTURE_QUERIES)
    assert 0.0 <= report["recall_at_k"] <= 1.0
    assert 0.0 <= report["mrr"] <= 1.0


def test_report_labels_are_honest() -> None:
    assert "not CI truth" in be.LABEL
