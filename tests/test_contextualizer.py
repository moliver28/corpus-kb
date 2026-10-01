"""TDD tests for the contextual-retrieval blurb generator."""

from __future__ import annotations

from pathlib import Path

from corpus_kb.rag.contextualizer import ContextGenerator

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
