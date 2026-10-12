"""TDD tests for the claim-decomposition/entailment judge."""

from __future__ import annotations

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
