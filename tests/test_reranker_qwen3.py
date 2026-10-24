"""U30/W3: OllamaReranker generative-binary verdict tests (offline).

W3 live verification against Ollama 0.31.1 (qwen3:4b, 2026-10-15) settled
the contract: token-logprob grading is transport-reachable (bool
``logprobs`` + ``top_logprobs`` answer 200) but semantically unusable —
the 4B revision reasons before any verdict, so the judgment position
carries no yes/no mass within a bounded budget, while mid-reasoning echo
words (the quoted "yes or no" instruction) carry P ~ 1.0 mass. The live
mode is generative-binary: one bounded generate with ``think`` off, the
verdict parsed from the response text, binary {1.0, 0.0}, neutral 0.5
when the text yields no unambiguous verdict.

The response texts below are REAL qwen3:4b completions captured live
(temperature 0, num_predict 96, think=False) — the recorded-fixture
pattern. qwen3-reranker itself is not pullable, so the default model
degrades to un-reranked RRF order; the scoring path is pinned here.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from corpus_kb.handlers.query_handler import QueryHandler
from corpus_kb.handlers.query_integrity import DegradedExcluded, SearchEnvelope
from corpus_kb.rag.rerank_settings import (
    RERANK_CONFIG_DEFAULTS,
    RerankSettings,
    load_rerank_settings,
)
from corpus_kb.rag.reranker import (
    GENERATIVE_JUDGMENT_TOKENS,
    RERANK_MODE_GENERATIVE_BINARY,
    OllamaReranker,
    _parse_verdict,
    build_reranker,
)
from tests.mock_pg import transaction_cm

# Recorded qwen3:4b completions (live, 2026-10-15; see module docstring).
_RECORDED_YES = (
    'First, the query is: "capital of France". I need to judge if the document meets the '
    "requirements based on this query.\n\n"
    'The document says: "The city of Paris is the capital and largest city of France, sitting '
    'on the Seine river."\n\n'
    "The query is asking for the capital of France. The document directly states that Paris is "
    "the capital of France. So, it provides the answer to the query.\n\n"
    'The instruction says: "Reply with exactly one word, yes or'
)
_RECORDED_NO = (
    'We are given a query: "capital of France"\n '
    'The document: "The mitochondria is the powerhouse of the cell."\n\n'
    " We are to judge whether the document meets the requirements based on the query.\n\n"
    " The query is asking for the capital of France. The document is about mitochondria and the "
    "powerhouse of the cell.\n\n"
    " There is no mention of France, its capital, or any related information in the document.\n\n"
    " Therefore, the document does not answer the query.\n\n"
    " We are to reply with"
)
_RECORDED_AMBIGUOUS = (
    'First, the query is: "capital of France". I need to judge if the document meets the '
    "requirements based on this query.\n\n"
    'The document says: "Paris is the capital of France."\n\n'
    'The relevance is given as: "Relevance:"\n\n'
    'But in the instruction, it says "Reply with exactly one word, yes or no." So I have to '
    'output either "yes" or "no".\n\n'
    'The key point is: does the document answer the query? The query is "'
)


def test_parse_verdict_accepts_single_word_and_variants() -> None:
    assert _parse_verdict("yes") == "yes"
    assert _parse_verdict("Yes.") == "yes"
    assert _parse_verdict("The answer is no.") == "no"
    assert _parse_verdict("NO") == "no"


def test_parse_verdict_stays_neutral_on_ambiguity_or_silence() -> None:
    # The echoed instruction carries both words: no readable verdict.
    assert _parse_verdict("reply with exactly one word, yes or no") is None
    assert _parse_verdict("") is None
    assert _parse_verdict("I cannot decide") is None


def test_judgment_score_reads_recorded_generative_verdicts(monkeypatch) -> None:
    reranker = _reranker_with_responses(monkeypatch, [_RECORDED_YES, _RECORDED_NO])
    scores = reranker.score("capital of France?", ["Paris is the capital.", "Mito chondria."])
    assert scores == [1.0, 0.0]
    assert reranker.last_not_evaluable == 0
    assert reranker.rerank_mode == RERANK_MODE_GENERATIVE_BINARY


def test_ambiguous_or_empty_responses_score_neutral(monkeypatch, caplog) -> None:
    reranker = _reranker_with_responses(monkeypatch, [_RECORDED_AMBIGUOUS, ""])
    with caplog.at_level("WARNING"):
        scores = reranker.score("q", ["rambling doc", "silent doc"])
    assert scores == [0.5, 0.5]
    assert reranker.last_not_evaluable == 2
    assert "not evaluable" in caplog.text


def test_generate_call_is_bounded_and_disables_thinking(monkeypatch) -> None:
    client = _FakeOllamaClient([_RECORDED_YES])
    reranker = _reranker_with_client(monkeypatch, client)
    reranker.score("q", ["doc"])
    call = client.calls[0]
    assert call["stream"] is False
    assert call["think"] is False
    assert call["options"] == {"temperature": 0, "num_predict": GENERATIVE_JUDGMENT_TOKENS}
    # The graded-logprob parameters are gone from the call shape.
    assert "logprobs" not in call
    assert "top_logprobs" not in call


def test_generate_retries_without_think_when_server_rejects(monkeypatch) -> None:
    class _NoThinkClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def generate(self, **kwargs: object) -> object:
            self.calls.append(kwargs)
            if "think" in kwargs:
                from ollama import ResponseError

                raise ResponseError("model does not support thinking", status_code=400)
            return SimpleNamespace(response="yes")

    client = _NoThinkClient()
    reranker = _reranker_with_client(monkeypatch, cast("_FakeOllamaClient", client))
    assert reranker.score("q", ["doc"]) == [1.0]
    assert len(client.calls) == 2
    assert "think" not in client.calls[1]


def test_score_caches_pairs(monkeypatch) -> None:
    client = _FakeOllamaClient([_RECORDED_YES])
    reranker = _reranker_with_client(monkeypatch, client)
    reranker.score("capital of France?", ["Paris is the capital."])
    reranker.score("capital of France?", ["Paris is the capital."])
    assert len(client.calls) == 1


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


class _FakeOllamaClient:
    """Sync ollama.Client stand-in returning recorded response texts in order."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def generate(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        text = self.responses.pop(0) if self.responses else ""
        return SimpleNamespace(response=text)


def _reranker_with_client(monkeypatch, client: object) -> OllamaReranker:
    monkeypatch.setattr("corpus_kb.rag.reranker.Client", lambda **kw: client)
    return OllamaReranker({"reranking": {"enabled": True, "model": "qwen3-reranker:4b"}})


def _reranker_with_responses(monkeypatch, responses: list[str]) -> OllamaReranker:
    return _reranker_with_client(monkeypatch, _FakeOllamaClient(responses))


def test_rerank_settings_defaults_and_reads() -> None:
    # Config v2: the module default flipped to enabled=True (the YAML drift
    # fix) and the block moved from search.rerank.* to reranking.*.
    assert RerankSettings() == RerankSettings(
        enabled=True,
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
        {"reranking": {"enabled": True, "candidates": 40, "calibration": "none"}}
    )
    assert loaded.enabled is True
    assert loaded.candidates == 40
    assert loaded.calibration == "none"
    assert RERANK_CONFIG_DEFAULTS["max_pair_tokens"] == 768
    assert RERANK_CONFIG_DEFAULTS["return_top_k"] == 20
    assert RERANK_CONFIG_DEFAULTS["over_retrieve_n"] == 60


def test_return_top_k_range_validation() -> None:
    """Range rule 1..1000; W3 made it the default return k (catalogue lockstep)."""
    assert load_rerank_settings({"reranking": {"return_top_k": 1}}).return_top_k == 1
    assert load_rerank_settings({"reranking": {"return_top_k": 1000}}).return_top_k == 1000
    with pytest.raises(ValueError, match="return_top_k"):
        load_rerank_settings({"reranking": {"return_top_k": 0}})
    with pytest.raises(ValueError, match="return_top_k"):
        load_rerank_settings({"reranking": {"return_top_k": 1001}})
    with pytest.raises(ValueError, match="return_top_k"):
        load_rerank_settings({"reranking": {"return_top_k": "20"}})


def test_rerank_settings_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="calibration"):
        load_rerank_settings({"reranking": {"calibration": "zscore"}})
    with pytest.raises(ValueError, match="candidates"):
        load_rerank_settings({"reranking": {"candidates": 0}})
    with pytest.raises(ValueError, match="score_floor"):
        load_rerank_settings({"reranking": {"score_floor": "low"}})


def test_rerank_block_ships_no_batch_size_anywhere() -> None:
    """OllamaReranker scores one pair per generate call, so
    search.rerank.batch_size was an inert knob (removed per the no-inert-
    knobs rule, 24d461c precedent). It must not ship in any of the three
    config locations."""
    from pathlib import Path

    import yaml

    from corpus_kb.config import get_default_config

    assert "batch_size" not in get_default_config()["reranking"]
    repo = Path(__file__).resolve().parent.parent
    for rel in ("config.yaml", Path("src") / "corpus_kb" / "config.yaml"):
        data = yaml.safe_load((repo / rel).read_text(encoding="utf-8"))
        assert "batch_size" not in data.get("reranking", {}), (
            f"{rel} still ships reranking.batch_size"
        )


def test_build_reranker_gate_respects_enabled_flag() -> None:
    # Config v2: enabled defaults TRUE, so an empty config builds the
    # (honestly-degrading) OllamaReranker; only enabled=False disables.
    assert build_reranker({"reranking": {"enabled": False}}) is None
    assert isinstance(build_reranker({}), OllamaReranker)
    assert isinstance(build_reranker({"reranking": {"enabled": True}}), OllamaReranker)
    fake = build_reranker({"reranking": {"enabled": True, "backend": "fake"}})
    assert hasattr(fake, "score")


def test_omitted_k_resolves_to_return_top_k() -> None:
    """W3: k=None (caller omitted) -> reranking.return_top_k; explicit wins."""
    from corpus_kb.domain.models import SearchQuery

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=transaction_cm())
    handler = _handler({"reranking": {"enabled": False, "return_top_k": 7}}, pool)
    resolved = handler._resolve_default_k(SearchQuery(query="q"))
    assert resolved.k == 7
    explicit = handler._resolve_default_k(SearchQuery(query="q", k=3))
    assert explicit.k == 3


def test_query_handler_prefers_reranking_block_over_legacy_selector() -> None:
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=transaction_cm())
    handler = _handler(
        {"reranking": {"enabled": True, "backend": "fake"}},
        pool,
    )
    assert hasattr(handler._reranker, "score")  # the reranking path won
    disabled = _handler({"reranking": {"enabled": False}, "search": {"reranker": "none"}}, pool)
    assert disabled._reranker is not None  # legacy identity fallback stands


@pytest.mark.asyncio
async def test_post_fuse_notes_generative_binary_mode() -> None:
    """The envelope note rides degraded_excluded.reasons (pinned key-set)."""
    from uuid import uuid4

    from corpus_kb.domain.models import SearchResult

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=transaction_cm())
    handler = _handler({"reranking": {"enabled": True, "backend": "fake"}}, pool)
    handler._reranker = cast("object", _NotedReranker())
    doc_id = str(uuid4())
    results = [
        SearchResult(chunk_id=str(uuid4()), text="a", score=1.0, source="s", doc_id=doc_id),
        SearchResult(chunk_id=str(uuid4()), text="b", score=0.5, source="s", doc_id=doc_id),
    ]
    reasons: list[str] = []
    out = await handler._post_fuse(results, "q", 2, 60, "tenant", reasons)
    assert len(out) == 1  # scored, sorted, floored
    assert out[0].score == 1.0
    assert reasons == ["rerank-mode-generative-binary"]


class _NotedReranker:
    """Minimal score-based reranker carrying the generative-binary mode."""

    rerank_mode = RERANK_MODE_GENERATIVE_BINARY

    def score(self, query: str, texts: list[str]) -> list[float]:
        return [1.0, 0.0]  # after score_floor 0.15 only the first survives


def test_envelope_note_rides_existing_reasons_field() -> None:
    from corpus_kb.handlers.query_integrity import envelope_to_payload

    envelope = SearchEnvelope(
        status="success",
        results=[],
        degraded_excluded=DegradedExcluded(count=0, reasons=["rerank-mode-generative-binary"]),
    )
    payload = envelope_to_payload(envelope)
    assert set(payload) == {
        "status",
        "result",
        "degraded_excluded",
        "vector_index_mode",
        "lexical_index_mode",
        "meta_hits",
    }
    assert payload["degraded_excluded"]["reasons"] == ["rerank-mode-generative-binary"]


def _handler(config: dict[str, object], pool: MagicMock) -> QueryHandler:
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return QueryHandler(cast("object", pool), reranker=None, config=config)
