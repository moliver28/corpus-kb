"""TDD tests for the claim-decomposition/entailment judge."""

from __future__ import annotations

import json

import httpx
import pytest

from corpus_kb.rag.judge import JUDGE_TIMEOUT_SECONDS, OllamaJudge


def test_entail_degrades_on_connection_error(monkeypatch) -> None:
    judge = OllamaJudge({"judge": {"enabled": True}})

    def _raise(*_a: object, **_k: object) -> None:
        raise ConnectionError("no ollama")

    monkeypatch.setattr(judge, "_client_chat", _raise)
    label, confidence = judge.entail("claim text", ["evidence"])
    assert label == "unsupported"
    assert confidence == 0.0


def test_decompose_degrades_to_empty_list_on_error(monkeypatch) -> None:
    judge = OllamaJudge({"judge": {"enabled": True}})

    def _raise(*_a: object, **_k: object) -> None:
        raise ConnectionError("no ollama")

    monkeypatch.setattr(judge, "_client_chat", _raise)
    assert judge.decompose("Some answer with two claims.") == []


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ReadTimeout("read timed out"),
        httpx.RemoteProtocolError("peer closed connection without sending complete message body"),
    ],
    ids=["wedged-server-timeout", "disconnecting-server"],
)
def test_transport_failures_degrade_instead_of_raising(monkeypatch, exc: Exception) -> None:
    """A wedged (accepts TCP, never answers) or disconnecting server surfaces
    as a TransportError; the judge must degrade to its fallbacks, never
    raise or hang (nli_client wedged-server lesson)."""
    judge = OllamaJudge({"judge": {"enabled": True}})

    def _raise(*_a: object, **_k: object) -> None:
        raise exc

    monkeypatch.setattr(judge, "_client_chat", _raise)
    assert judge.decompose("Some answer with two claims.") == []
    assert judge.entail("claim text", ["evidence"]) == ("unsupported", 0.0)


def test_client_is_constructed_with_explicit_timeout(monkeypatch) -> None:
    """The ollama client waits forever without a bound: construction must
    carry JUDGE_TIMEOUT_SECONDS (120s, nli_client precedent)."""
    captured: dict[str, object] = {}

    def _fake_client(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("corpus_kb.rag.judge.Client", _fake_client)
    OllamaJudge({"judge": {"base_url": "http://localhost:9999"}})
    assert captured == {
        "host": "http://localhost:9999",
        "timeout": JUDGE_TIMEOUT_SECONDS,
    }
    assert JUDGE_TIMEOUT_SECONDS == 120.0


# ---------------------------------------------------------------------------
# entail_batch: many claims against one evidence slice, ONE chat call
# ---------------------------------------------------------------------------


def _canned_chat(judge: OllamaJudge, monkeypatch, raw: str) -> list[tuple[str, str]]:
    """Stub the chat seam; record every (system, user) call, return raw."""
    calls: list[tuple[str, str]] = []

    def _chat(system: str, user: str) -> str:
        calls.append((system, user))
        return raw

    monkeypatch.setattr(judge, "_client_chat", _chat)
    return calls


def test_entail_batch_labels_every_claim_in_one_call(monkeypatch) -> None:
    """One chat call carries the evidence and the numbered claims; the
    verdicts come back per claim, in claim order."""
    judge = OllamaJudge({"judge": {"enabled": True}})
    raw = json.dumps(
        [
            {"n": 1, "label": "entailed", "confidence": 0.9},
            {"n": 2, "label": "contradicted", "confidence": 0.8},
        ]
    )
    calls = _canned_chat(judge, monkeypatch, raw)

    out = judge.entail_batch(["alpha holds", "beta fails"], ["shared evidence"])

    assert out == [("entailed", 0.9), ("contradicted", 0.8)]
    assert len(calls) == 1
    system, user = calls[0]
    assert "shared evidence" in system
    assert "1. alpha holds" in user
    assert "2. beta fails" in user


def test_entail_batch_degrades_all_on_transport_error(monkeypatch) -> None:
    """A wedged or down server degrades the whole batch to unsupported,
    never raises (the judge's never-raises contract)."""
    judge = OllamaJudge({"judge": {"enabled": True}})

    def _raise(*_a: object, **_k: object) -> str:
        raise httpx.ReadTimeout("read timed out")

    monkeypatch.setattr(judge, "_client_chat", _raise)
    assert judge.entail_batch(["a", "b"], ["ev"]) == [("unsupported", 0.0), ("unsupported", 0.0)]


def test_entail_batch_degrades_missing_and_malformed_entries(monkeypatch) -> None:
    """A claim with no usable entry degrades alone; a digit-string claim
    number is accepted, a bad label or an out-of-range confidence is not."""
    judge = OllamaJudge({"judge": {"enabled": True}})
    raw = json.dumps(
        [
            {"n": 2, "label": "entailed", "confidence": 0.7},
            {"n": "1", "label": "bogus", "confidence": 2.5},
        ]
    )
    _canned_chat(judge, monkeypatch, raw)

    out = judge.entail_batch(["claim one", "claim two"], ["ev"])

    assert out == [("unsupported", 0.0), ("entailed", 0.7)]


def test_entail_batch_ignores_out_of_range_claim_numbers(monkeypatch) -> None:
    judge = OllamaJudge({"judge": {"enabled": True}})
    raw = json.dumps([{"n": 9, "label": "entailed", "confidence": 1.0}])
    _canned_chat(judge, monkeypatch, raw)

    assert judge.entail_batch(["only claim"], ["ev"]) == [("unsupported", 0.0)]


def test_entail_batch_non_list_response_degrades_all(monkeypatch) -> None:
    judge = OllamaJudge({"judge": {"enabled": True}})
    _canned_chat(judge, monkeypatch, '{"label": "entailed", "confidence": 1.0}')

    out = judge.entail_batch(["claim"], ["ev"])

    assert out == [("unsupported", 0.0)]


def test_entail_non_dict_json_degrades(monkeypatch) -> None:
    """The single-claim seam applies the same honesty rule to a JSON list."""
    judge = OllamaJudge({"judge": {"enabled": True}})
    raw = json.dumps([{"label": "entailed", "confidence": 1.0}])
    _canned_chat(judge, monkeypatch, raw)

    assert judge.entail("claim text", ["evidence"]) == ("unsupported", 0.0)


# ---------------------------------------------------------------------------
# _client_chat: the ollama client response shapes
# ---------------------------------------------------------------------------


def test_client_chat_reads_pydantic_style_response(monkeypatch) -> None:
    """ollama>=0.4 returns ChatResponse objects; a dict-only reader would
    take the '[]' default and the judge would abstain on every live call."""
    from types import SimpleNamespace

    judge = OllamaJudge({"judge": {"enabled": True}})
    fake = SimpleNamespace(
        chat=lambda **_kwargs: SimpleNamespace(message=SimpleNamespace(content='["claim one"]'))
    )
    monkeypatch.setattr(judge, "_client", fake)

    assert judge.decompose("answer") == ["claim one"]


def test_client_chat_reads_dict_style_response(monkeypatch) -> None:
    """Older dict-shaped client responses stay supported."""
    from types import SimpleNamespace

    judge = OllamaJudge({"judge": {"enabled": True}})
    fake = SimpleNamespace(
        chat=lambda **_kwargs: {"message": {"content": '{"label": "entailed", "confidence": 0.5}'}}
    )
    monkeypatch.setattr(judge, "_client", fake)

    assert judge.entail("claim", ["evidence"]) == ("entailed", 0.5)
