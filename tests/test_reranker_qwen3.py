"""U30: qwen3-reranker logprob judgment scoring tests (offline).

The reranker contract was verified live against Ollama 0.31.1 on 2026-10-10:
``/api/generate`` accepts ``logprobs=true`` (bool — a number is rejected with
a Go unmarshal error) plus ``top_logprobs=<int>``, and each response
``logprobs[]`` entry carries ``{token, logprob, bytes, top_logprobs[]}``.
qwen3-reranker itself was NOT on the registry at implementation time
("pull model manifest: file does not exist"), so the production default
model degrades to un-reranked RRF order with a logged warning; the scoring
path is pinned here against canned logprob shapes captured from a live
qwen3:4b stand-in.
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from corpus_kb.handlers.query_handler import QueryHandler
from corpus_kb.rag.rerank_settings import (
    RERANK_CONFIG_DEFAULTS,
    RerankSettings,
    load_rerank_settings,
)
from corpus_kb.rag.reranker import (
    MAX_JUDGMENT_TOKENS,
    OllamaReranker,
    _judgment_score,
    _logprob_entries,
    _normalize_judgment,
    build_reranker,
)
from tests.mock_pg import transaction_cm


def _alt(token: str, logprob: float) -> SimpleNamespace:
    return SimpleNamespace(token=token, logprob=logprob)


def _position(*alts: tuple[str, float]) -> SimpleNamespace:
    greedy = alts[0]
    return SimpleNamespace(
        token=greedy[0],
        logprob=greedy[1],
        top_logprobs=[_alt(t, lp) for t, lp in alts],
    )


def test_normalize_judgment_handles_tokenizer_variants() -> None:
    assert _normalize_judgment(" yes") == "yes"
    assert _normalize_judgment("Yes.") == "yes"
    assert _normalize_judgment("NO") == "no"
    assert _normalize_judgment(",") == ""


def test_judgment_score_is_exact_yes_no_probability_ratio() -> None:
    # Captured shape: " yes" -0.0991, " no" -4.5603 (live probe).
    p_yes = math.exp(-0.0991)
    p_no = math.exp(-4.5603)
    score = _judgment_score([[_alt(" yes", -0.0991), _alt(" no", -4.5603)]])
    assert score == pytest.approx(p_yes / (p_yes + p_no))


def test_judgment_score_scans_past_preamble_positions() -> None:
    # Position 0 is a preamble token; position 1 holds the judgment.
    positions = [
        [_alt(" The", -0.4), _alt(" This", -1.1)],
        [_alt(" yes", -0.2), _alt(" no", -3.0)],
    ]
    p_yes = math.exp(-0.2)
    p_no = math.exp(-3.0)
    assert _judgment_score(positions) == pytest.approx(p_yes / (p_yes + p_no))
    # A single position carrying no yes/no mass at all is not_evaluable.
    assert _judgment_score([[_alt(" The", -0.4)]]) is None


def test_judgment_score_prefers_position_with_most_judgment_mass() -> None:
    # "yes" appears twice; the concentrated judgment position must win.
    weak = [_alt("yes", -6.0), _alt("no", -7.0)]
    strong = [_alt(" yes", -0.1), _alt(" no", -5.0)]
    expected = math.exp(-0.1) / (math.exp(-0.1) + math.exp(-5.0))
    assert _judgment_score([weak, strong]) == pytest.approx(expected)


def test_logprob_entries_reads_both_object_and_dict_responses() -> None:
    obj_response = SimpleNamespace(logprobs=[_position((" yes", -0.1))])
    dict_response = {
        "logprobs": [
            {
                "token": " yes",
                "logprob": -0.1,
                "top_logprobs": [{"token": " yes", "logprob": -0.1}],
            }
        ]
    }
    assert len(_logprob_entries(obj_response)) == 1
    assert len(_logprob_entries(dict_response)) == 1
    assert _logprob_entries({"logprobs": None}) == []
    assert _judgment_score(_logprob_entries(dict_response)) == pytest.approx(1.0)


class _FakeOllamaClient:
    """Sync ollama.Client stand-in returning canned judgment logprobs."""

    def __init__(self, positions: list[SimpleNamespace] | None = None) -> None:
        self.positions = positions or []
        self.calls: list[dict[str, object]] = []

    def generate(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(response="", logprobs=self.positions)


def _reranker_with_client(monkeypatch, client: _FakeOllamaClient) -> OllamaReranker:
    monkeypatch.setattr("corpus_kb.rag.reranker.Client", lambda **kw: client)
    return OllamaReranker({"search": {"rerank": {"enabled": True, "model": "qwen3-reranker:4b"}}})


def test_score_uses_judgment_probabilities_and_caches(monkeypatch) -> None:
    client = _FakeOllamaClient([_position((" yes", -0.1), (" no", -4.6), (" Yes", -2.8))])
    reranker = _reranker_with_client(monkeypatch, client)
    scores = reranker.score("capital of France?", ["Paris is the capital."])
    assert scores is not None and len(scores) == 1
    p_yes = math.exp(-0.1) + math.exp(-2.8)
    p_no = math.exp(-4.6)
    assert scores[0] == pytest.approx(p_yes / (p_yes + p_no))
    # Second call hits the cache: still one generate.
    reranker.score("capital of France?", ["Paris is the capital."])
    assert len(client.calls) == 1
    # The generate is bounded: temperature 0, capped tokens, logprobs on.
    call = client.calls[0]
    assert call["logprobs"] is True
    assert call["options"] == {"temperature": 0, "num_predict": MAX_JUDGMENT_TOKENS}
    assert call["top_logprobs"] == 20


def test_score_counts_not_evaluable_pairs(monkeypatch, caplog) -> None:
    client = _FakeOllamaClient([_position((" The", -0.3), (" document", -0.9))])
    reranker = _reranker_with_client(monkeypatch, client)
    with caplog.at_level("WARNING"):
        scores = reranker.score("q", ["irrelevant rambling doc"])
    assert scores == [0.5]
    assert reranker.last_not_evaluable == 1
    assert "not evaluable" in caplog.text


def test_score_degrades_to_none_when_model_not_pulled(monkeypatch, caplog) -> None:
    from ollama import ResponseError

    class _MissingModelClient:
        @staticmethod
        def generate(**kwargs: object) -> object:
            raise ResponseError("model 'qwen3-reranker:4b' not found, try pulling it first")

    reranker = _reranker_with_client(monkeypatch, _MissingModelClient())
    with caplog.at_level("WARNING"):
        scores = reranker.score("q", ["doc"])
    assert scores is None  # pass-through un-reranked, never raised
    assert "Reranker unavailable" in caplog.text


def test_score_degrades_to_none_on_wedged_server(monkeypatch) -> None:
    class _WedgedClient:
        @staticmethod
        def generate(**kwargs: object) -> object:
            raise httpx.TimeoutException("timed out")

    reranker = _reranker_with_client(monkeypatch, _WedgedClient())
    assert reranker.score("q", ["doc"]) is None


def test_rerank_settings_defaults_and_reads() -> None:
    assert RerankSettings() == RerankSettings(
        enabled=False,
        model="qwen3-reranker:4b",
        base_url="http://localhost:11434",
        candidates=100,
        max_pair_tokens=768,
        score_floor=0.15,
        calibration="minmax",
        timeout_seconds=120.0,
    )
    assert load_rerank_settings({}) == RerankSettings()
    loaded = load_rerank_settings(
        {"search": {"rerank": {"enabled": True, "candidates": 40, "calibration": "none"}}}
    )
    assert loaded.enabled is True
    assert loaded.candidates == 40
    assert loaded.calibration == "none"
    assert RERANK_CONFIG_DEFAULTS["max_pair_tokens"] == 768


def test_rerank_settings_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="calibration"):
        load_rerank_settings({"search": {"rerank": {"calibration": "zscore"}}})
    with pytest.raises(ValueError, match="candidates"):
        load_rerank_settings({"search": {"rerank": {"candidates": 0}}})
    with pytest.raises(ValueError, match="score_floor"):
        load_rerank_settings({"search": {"rerank": {"score_floor": "low"}}})


def test_build_reranker_gate_respects_enabled_flag() -> None:
    assert build_reranker({}) is None
    assert isinstance(build_reranker({"search": {"rerank": {"enabled": True}}}), OllamaReranker)
    fake = build_reranker({"search": {"rerank": {"enabled": True, "backend": "fake"}}})
    assert hasattr(fake, "score")


def _handler(config: dict[str, object], pool: MagicMock) -> QueryHandler:
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return QueryHandler(cast("object", pool), reranker=None, config=config)


def test_query_handler_prefers_rerank_block_over_legacy_selector() -> None:
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=transaction_cm())
    handler = _handler(
        {"search": {"reranker": "none", "rerank": {"enabled": True, "backend": "fake"}}},
        pool,
    )
    assert hasattr(handler._reranker, "score")  # the search.rerank path won
    disabled = _handler({"search": {"reranker": "none"}}, pool)
    assert disabled._reranker is not None  # legacy identity fallback stands
