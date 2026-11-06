"""The routing calibration sweep against a deterministic fake embedder.

The sweep routes the golden questions once per (threshold, margin) grid
point and Holm-compares the accuracy. P-values come from a patched
``_ranx_p_value`` (the ranx-absent path is forced through the sign test
branch), so no test here needs the calibrate extra or Ollama.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

import pytest

from corpus_kb.analysis.method_register import MethodRegister
from corpus_kb.errors import ValidationError
from corpus_kb.eval.routing_bench_types import RoutingGoldenRow
from corpus_kb.eval.routing_calibrate import run_routing_calibration

# Method 05 owns the cluster direction (vector e1), method 01 the
# codebook direction (e2). The tie question sits at cosine 0.50 / 0.45:
# the baseline knob misses it, the (0.45, 0.05) knob resolves it.
_CLUSTER = [1.0, 0.0, 0.0]
_CODEBOOK = [0.0, 1.0, 0.0]
_TIE = [0.5, 0.45, 0.7399324016683636]  # unit norm: 0.25 + 0.2025 + w^2 = 1

_TIE_QUESTION = "What clustering approach fits my files?"
_CLUSTER_QUESTION = "How do I cluster the documents by theme?"
_CODEBOOK_QUESTION = "Can I build a codebook for interviews?"

_GOLDEN = (
    RoutingGoldenRow(question=_CLUSTER_QUESTION, expected_method_ids=("05",)),
    RoutingGoldenRow(question=_CODEBOOK_QUESTION, expected_method_ids=("01",)),
    RoutingGoldenRow(question=_TIE_QUESTION, expected_method_ids=("05", "01")),
)


class _SplitEmbedder:
    """Cluster text maps to e1, codebook text to e2, the tie to 0.50/0.45."""

    def embed(self, text: str) -> list[float]:
        folded = text.casefold()
        if text == _TIE_QUESTION.casefold():  # the router normalizes first
            return list(_TIE)
        if "cluster" in folded:
            return list(_CLUSTER)
        if "codebook" in folded:
            return list(_CODEBOOK)
        return [0.0, 0.0, 1.0]


class _ZeroEmbedder:
    """The degraded-embedder signal: every vector is the zero vector."""

    def embed(self, text: str) -> list[float]:
        return [0.0, 0.0, 0.0]


def _register() -> MethodRegister:
    row: dict[str, object] = {
        "id": "05",
        "name": "Cluster method",
        "summary": "A short active summary for the test row.",
        "paradigm": "unsupervised",
        "evidence_label": "model_based",
        "inputs_required": ["raw_text"],
        "calibration_effort": "low",
        "autonomy": "automated_with_audit",
        "complexity_tier": "baseline",
        "selection_priority": "first",
        "selection_notes": "First for the test need.",
        "robustness_caveat": "Results change with the input wording.",
        "bias_risk": "The method can favor common wording.",
        "efficiency_workload": "One pass over the records.",
        "output_family": "scores",
        "output_shape": "One score per record.",
        "usage_guidance": "Use it for quick screening.",
        "validation_required": "Check a sample of the scores.",
        "permitted_claims": "Screening claims only.",
        "do_not_claim": "Do not claim calibrated error rates.",
        "implementation_status": "not_planned",
        "op": None,
        "sources": ["https://example.com/method"],
        "question_phrases": [
            "cluster the documents by theme?",
            "Which cluster themes recur?",
        ],
    }
    other = dict(row)
    other["id"] = "01"
    other["name"] = "Codebook method"
    other["question_phrases"] = ["build a codebook for interviews?"]
    return MethodRegister.model_validate(
        {
            "register_version": "1.1.0",
            "source_document": {"title": "Test document", "path_note": "Test rows only."},
            "selection_rules": ["Match the required output."],
            "adjacent_candidates": "None.",
            "methods": [row, other],
        }
    )


_CFG: dict[str, object] = {
    "embedding": {"model": "fake-model", "dimensions": 3},
    "reranking": {"enabled": False},
}


def _patch_p(monkeypatch: pytest.MonkeyPatch, table: dict[str, float]) -> None:
    def fake_p(
        query_scores: object, baseline: str, challenger: str, metric: str, k: int | None
    ) -> float | None:
        return table.get(challenger, 0.4)

    monkeypatch.setattr("corpus_kb.eval.stats._ranx_p_value", fake_p)


def _golden_sha() -> str:
    return hashlib.sha256(b"routing-golden").hexdigest()


def _profile_world(tmp_path: Path) -> tuple[Path, str]:
    """One profile file in tmp; returns its path and its byte hash."""
    profile_path = tmp_path / "routing.yaml"
    profile_path.write_text("data_type: transcript\n", encoding="utf-8")
    sha = hashlib.sha256(profile_path.read_bytes()).hexdigest()
    return profile_path, sha


async def test_routing_sweep_picks_significant_winner_and_writes_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The (0.45, 0.05) knob resolves the tie; the lock records the knobs."""
    # Holm scales the smallest p by the challenger count (7): 7 * 0.001
    # clears the 0.05 bar, so the fixture pins the correction too.
    _patch_p(monkeypatch, {"t045_m05": 0.001})
    lock_path = tmp_path / "routing.lock.json"
    _, profile_sha = _profile_world(tmp_path)
    result = await run_routing_calibration(
        golden=_GOLDEN,
        register=_register(),
        embedder=_SplitEmbedder(),
        cfg=_CFG,
        golden_sha256=_golden_sha(),
        lock_path=lock_path,
        profile_name="routing",
        profile_sha256=profile_sha,
        min_queries=2,
    )
    assert result.status == "success"
    assert result.chosen == "t045_m05"
    payload = dict(json.loads(lock_path.read_text(encoding="utf-8")))
    assert payload["outcome"] == "success"
    assert payload["chosen"] == "t045_m05"
    assert payload["sweep"] == "routing"
    assert payload["sample"] == []
    assert payload["proposed_overrides"] == {
        "analysis.routing.confidence_threshold": 0.45,
        "analysis.routing.margin": 0.05,
    }
    scores = {row.candidate: row for row in result.candidates}
    assert scores["t055_m10"].ndcg_at_10 == pytest.approx(2 / 3)
    assert scores["t045_m05"].ndcg_at_10 == pytest.approx(1.0)


async def test_routing_sweep_no_significant_winner_writes_no_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A flat family claims no winner; the lock path stays untouched."""
    _patch_p(monkeypatch, {"t045_m05": 0.4, "t045_m10": 0.4, "t050_m10": 0.4})
    lock_path = tmp_path / "routing.lock.json"
    _, profile_sha = _profile_world(tmp_path)
    result = await run_routing_calibration(
        golden=_GOLDEN,
        register=_register(),
        embedder=_SplitEmbedder(),
        cfg=_CFG,
        golden_sha256=_golden_sha(),
        lock_path=lock_path,
        profile_name="routing",
        profile_sha256=profile_sha,
        min_queries=2,
    )
    assert result.status == "no_significant_winner"
    assert result.chosen is None
    assert not lock_path.exists()


async def test_routing_sweep_sign_test_refusal_writes_no_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No ranx: no p-value, honest refusal, no lock."""
    monkeypatch.setattr("corpus_kb.eval._ranx.ranx_available", lambda: False)
    lock_path = tmp_path / "routing.lock.json"
    _, profile_sha = _profile_world(tmp_path)
    result = await run_routing_calibration(
        golden=_GOLDEN,
        register=_register(),
        embedder=_SplitEmbedder(),
        cfg=_CFG,
        golden_sha256=_golden_sha(),
        lock_path=lock_path,
        profile_name="routing",
        profile_sha256=profile_sha,
        min_queries=2,
    )
    assert result.status == "insufficient_data"
    assert result.reason is not None and "sign test" in result.reason
    assert not lock_path.exists()


async def test_routing_sweep_requires_a_live_embedder(tmp_path: Path) -> None:
    """embedder=None is an honest refusal: phrase numbers are not the sweep."""
    result = await run_routing_calibration(
        golden=_GOLDEN,
        register=_register(),
        embedder=None,
        cfg=_CFG,
        golden_sha256=_golden_sha(),
        min_queries=2,
    )
    assert result.status == "insufficient_data"
    assert result.reason is not None and "embedding" in result.reason


async def test_routing_sweep_refuses_degraded_phrase_mode(tmp_path: Path) -> None:
    """A zero-vector embedder degrades to phrase mode; the sweep refuses."""
    result = await run_routing_calibration(
        golden=_GOLDEN,
        register=_register(),
        embedder=_ZeroEmbedder(),
        cfg=_CFG,
        golden_sha256=_golden_sha(),
        min_queries=2,
    )
    assert result.status == "insufficient_data"
    assert result.reason is not None and "zero-vector" in result.reason


async def test_routing_sweep_empty_golden_refuses(tmp_path: Path) -> None:
    result = await run_routing_calibration(
        golden=(),
        register=_register(),
        embedder=_SplitEmbedder(),
        cfg=_CFG,
        golden_sha256=_golden_sha(),
        min_queries=2,
    )
    assert result.status == "insufficient_data"


async def test_routing_sweep_caps_the_grid(tmp_path: Path) -> None:
    """A nine-point grid breaks the family cap: loud refusal, no sweep."""
    grid = [(0.5, 0.1)] * 9
    with pytest.raises(ValidationError, match="8"):
        await run_routing_calibration(
            golden=_GOLDEN,
            register=_register(),
            embedder=_SplitEmbedder(),
            cfg=_CFG,
            golden_sha256=_golden_sha(),
            grid=cast("list[tuple[float, float]]", grid),
            min_queries=2,
        )


async def test_routing_sweep_lock_needs_profile_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lock path without the profile hash is a typed refusal."""
    _patch_p(monkeypatch, {"t045_m05": 0.001})
    with pytest.raises(ValidationError, match="profile"):
        await run_routing_calibration(
            golden=_GOLDEN,
            register=_register(),
            embedder=_SplitEmbedder(),
            cfg=_CFG,
            golden_sha256=_golden_sha(),
            lock_path=tmp_path / "routing.lock.json",
            min_queries=2,
        )
