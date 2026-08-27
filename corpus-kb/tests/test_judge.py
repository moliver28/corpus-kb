"""TDD tests for the claim-decomposition/entailment judge."""

from __future__ import annotations

from src.rag.judge import OllamaJudge


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
