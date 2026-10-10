"""TDD tests for the contextual-retrieval blurb generator."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from corpus_kb.rag.contextualizer import CONTEXTUAL_TIMEOUT_SECONDS, ContextGenerator

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "contextual_recorded"


def test_missing_fixture_with_live_fallback_false_returns_empty() -> None:
    gen = ContextGenerator(
        {"contextual": {"fixture_dir": str(_FIXTURE_DIR), "live_fallback": False}}
    )
    assert gen.generate_blurb("full document text", "a chunk with no fixture") == ""


def test_client_error_degrades_to_empty_string(monkeypatch) -> None:
    gen = ContextGenerator(
        {"contextual": {"live_fallback": True, "fixture_dir": str(_FIXTURE_DIR)}}
    )

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise ConnectionError("no ollama")

    monkeypatch.setattr(gen, "_client_generate", _raise)
    assert gen.generate_blurb("doc", "chunk") == ""


def test_generate_blurbs_returns_one_per_chunk() -> None:
    gen = ContextGenerator(
        {"contextual": {"fixture_dir": str(_FIXTURE_DIR), "live_fallback": False}}
    )
    blurbs = gen.generate_blurbs("doc text", ["chunk one", "chunk two"])
    assert blurbs == ["", ""]


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ReadTimeout("read timed out"),
        httpx.RemoteProtocolError("peer closed connection without sending complete message body"),
    ],
    ids=["wedged-server-timeout", "disconnecting-server"],
)
def test_transport_failures_degrade_to_empty_blurb(monkeypatch, exc: Exception) -> None:
    """A wedged or disconnecting server (TransportError) must degrade to an
    empty blurb, never raise into ingest or hang (nli_client lesson)."""
    gen = ContextGenerator(
        {"contextual": {"live_fallback": True, "fixture_dir": str(_FIXTURE_DIR)}}
    )

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise exc

    monkeypatch.setattr(gen, "_client_generate", _raise)
    assert gen.generate_blurb("doc", "chunk") == ""


def test_client_is_constructed_with_explicit_timeout(monkeypatch) -> None:
    """The ollama client waits forever without a bound: construction must
    carry CONTEXTUAL_TIMEOUT_SECONDS (120s, nli_client precedent)."""
    captured: dict[str, object] = {}

    def _fake_client(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("corpus_kb.rag.contextualizer.Client", _fake_client)
    ContextGenerator({"contextual": {"base_url": "http://localhost:9999"}})
    assert captured == {
        "host": "http://localhost:9999",
        "timeout": CONTEXTUAL_TIMEOUT_SECONDS,
    }
    assert CONTEXTUAL_TIMEOUT_SECONDS == 120.0
