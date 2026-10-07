"""Offline tests for tier-3 self-consistency (todo 16) — recorded NLI fixture.

All NLI judgments come from tests/fixtures/research/nli_recorded_qwen3.json
(real qwen3:4b judgments captured with the fixed prompt; r11 CI contract:
no network in tests). The live-NLI path is tests/test_tier3_live_nli.py
(requires_ollama).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from corpus_kb.coding.uncertainty.nli_client import (
    NLI_PROMPT_ID,
    NLI_PROMPT_TEMPLATE,
    fixture_judge,
    parse_nli_reply,
)
from corpus_kb.coding.uncertainty.tier3_consistency import (
    N_SAMPLES,
    DeferredTierError,
    normalized_semantic_entropy,
    require_supported_tier,
    self_consistency,
)

_FIXTURE = Path(__file__).with_name("fixtures") / "research" / "nli_recorded_qwen3.json"


def _load_fixture() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def test_prompt_id_pins_the_fixed_prompt():
    import hashlib

    assert hashlib.sha256(NLI_PROMPT_TEMPLATE.encode("utf-8")).hexdigest()[:16] == NLI_PROMPT_ID
    assert len(NLI_PROMPT_ID) == 16


def test_parse_nli_reply_tolerates_think_blocks():
    assert parse_nli_reply("ENTAIL") is True
    assert parse_nli_reply("CONTRADICT") is False
    assert parse_nli_reply("<think>blah ENTAIL blah</think>\nCONTRADICT") is False
    assert parse_nli_reply("<think>reasoning only</think>\nENTAIL") is True
    assert parse_nli_reply("I cannot decide") is None


def test_fixture_judge_is_symmetric_and_strict():
    doc = _load_fixture()
    unit = doc["units"][0]
    judge = fixture_judge(unit["records"])
    first = unit["records"][0]
    assert judge(first["a"], first["b"]) == bool(first["entails"])
    assert judge(first["b"], first["a"]) == bool(first["entails"])
    with pytest.raises(KeyError):
        judge("unrecorded rationale one", "unrecorded rationale two")


def test_all_fixture_judgments_parsed():
    doc = _load_fixture()
    for unit in doc["units"]:
        for record in unit["records"]:
            assert record["entails"] is not None, record
            assert record["entails"] == parse_nli_reply(str(record["raw"]))


def test_recorded_ambiguous_unit_yields_two_clusters():
    doc = _load_fixture()
    unit = next(u for u in doc["units"] if u["unit"] == "ambiguous_two_themes")
    judge = fixture_judge(unit["records"])
    result = self_consistency(unit["rationales"], judge)
    assert result.n_samples == N_SAMPLES == 5
    assert result.n_nli_calls == 10
    assert result.n_clusters == 2
    assert result.cluster_sizes == [3, 2]
    assert result.semantic_entropy == pytest.approx(normalized_semantic_entropy([3, 2], 5))


def test_recorded_clear_unit_yields_one_cluster_zero_entropy():
    doc = _load_fixture()
    unit = next(u for u in doc["units"] if u["unit"] == "clear_denial")
    judge = fixture_judge(unit["records"])
    result = self_consistency(unit["rationales"], judge)
    assert result.n_clusters == 1
    assert result.cluster_sizes == [5]
    assert result.semantic_entropy == pytest.approx(0.0)


def test_transitive_closure_merges_chained_entailment():
    result = self_consistency(["a1", "a2", "a3", "b1"], lambda x, y: x[0] == y[0])
    assert result.n_clusters == 2
    assert result.cluster_sizes == [3, 1]
    assert result.semantic_entropy == pytest.approx(normalized_semantic_entropy([3, 1], 4))


def test_normalized_semantic_entropy_bounds_and_math():
    assert normalized_semantic_entropy([5], 5) == 0.0
    assert normalized_semantic_entropy([1] * 5, 5) == pytest.approx(1.0)
    assert normalized_semantic_entropy([], 5) == 0.0
    assert normalized_semantic_entropy([1], 1) == 0.0
    with pytest.raises(ValueError):
        normalized_semantic_entropy([2, 2], 5)


def test_deferred_tier_guard_hard_errors():
    assert require_supported_tier(0) == 0
    assert require_supported_tier(3) == 3
    for tier in (4, 5, 99):
        with pytest.raises(DeferredTierError):
            require_supported_tier(tier)


def test_empty_resamples_degrade_cleanly():
    result = self_consistency([], lambda x, y: True)
    assert result.semantic_entropy == 0.0
    assert result.n_clusters == 0
    assert result.n_nli_calls == 0
