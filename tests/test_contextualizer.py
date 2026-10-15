"""TDD tests for the contextual-retrieval blurb generator (W3 contextualize v2)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import yaml

from corpus_kb.rag.contextual_settings import (
    CONTEXTUAL_CONFIG_DEFAULTS,
    ContextualSettings,
    contextualize_settings_error,
    load_contextual_settings,
)
from corpus_kb.rag.contextualizer import (
    CONTEXTUAL_TIMEOUT_SECONDS,
    PROMPT_TEMPLATES,
    ContextGenerator,
    _cap_blurb,
    _fixture_key,
)

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "contextual_recorded"


def test_missing_fixture_with_live_fallback_false_returns_empty() -> None:
    gen = ContextGenerator(
        {"ingest": {"contextualize": {"fixture_dir": str(_FIXTURE_DIR), "live_fallback": False}}}
    )
    assert gen.generate_blurb("full document text", "a chunk with no fixture") == ""


def test_client_error_degrades_to_empty_string(monkeypatch) -> None:
    gen = ContextGenerator(
        {"ingest": {"contextualize": {"live_fallback": True, "fixture_dir": str(_FIXTURE_DIR)}}}
    )

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise ConnectionError("no ollama")

    monkeypatch.setattr(gen, "_client_generate", _raise)
    assert gen.generate_blurb("doc", "chunk") == ""


def test_generate_blurbs_returns_one_per_chunk() -> None:
    gen = ContextGenerator(
        {"ingest": {"contextualize": {"fixture_dir": str(_FIXTURE_DIR), "live_fallback": False}}}
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
        {"ingest": {"contextualize": {"live_fallback": True, "fixture_dir": str(_FIXTURE_DIR)}}}
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
    ContextGenerator({"ingest": {"contextualize": {"base_url": "http://localhost:9999"}}})
    assert captured == {
        "host": "http://localhost:9999",
        "timeout": CONTEXTUAL_TIMEOUT_SECONDS,
    }
    assert CONTEXTUAL_TIMEOUT_SECONDS == 120.0


# ---------------------------------------------------------------------------
# W3: prompt profiles x orderings — the four-template matrix
# ---------------------------------------------------------------------------


def test_template_matrix_covers_every_profile_ordering_pair() -> None:
    assert set(PROMPT_TEMPLATES) == {
        ("interview", True),
        ("interview", False),
        ("generic", True),
        ("generic", False),
    }
    # Every template renders with the same two slots.
    for template in PROMPT_TEMPLATES.values():
        rendered = template.format(document="DOC", chunk="CHUNK")
        assert "DOC" in rendered and "CHUNK" in rendered


def test_interview_profile_names_the_moderator_question() -> None:
    interview = PROMPT_TEMPLATES[("interview", True)]
    assert "moderator" in interview
    # The generic profile stays neutral: no transcript-specific language.
    generic = PROMPT_TEMPLATES[("generic", True)]
    assert "moderator" not in generic


def test_document_first_true_puts_document_block_first(monkeypatch) -> None:
    captured = _capture_prompt(monkeypatch, {"document_first_prompt": True})
    assert captured.index("<document>") < captured.index("<chunk>")


def test_document_first_false_puts_chunk_block_first(monkeypatch) -> None:
    captured = _capture_prompt(monkeypatch, {"document_first_prompt": False})
    assert captured.index("<chunk>") < captured.index("<document>")


def test_profile_selects_the_template(monkeypatch) -> None:
    assert "moderator" in _capture_prompt(monkeypatch, {"prompt_profile": "interview"})
    assert "moderator" not in _capture_prompt(monkeypatch, {"prompt_profile": "generic"})


def _capture_prompt(monkeypatch, overrides: dict[str, object]) -> str:
    """Run one live-path blurb with a canned client; return the prompt."""
    config: dict[str, object] = {
        "ingest": {
            "contextualize": {
                "live_fallback": True,
                "fixture_dir": None,
                **overrides,
            }
        }
    }
    gen = ContextGenerator(config)
    captured: dict[str, object] = {}

    def _fake_generate(**kwargs: object) -> dict[str, str]:
        captured.update(kwargs)
        return {"response": "A succinct context."}

    monkeypatch.setattr(gen, "_client", SimpleGenerateClient(_fake_generate))
    blurb = gen.generate_blurb("the document body", "the chunk body")
    assert blurb == "A succinct context."
    return str(captured["prompt"])


class SimpleGenerateClient:
    """Minimal ollama Client stand-in capturing generate kwargs."""

    def __init__(self, fake_generate: object) -> None:
        self._fake_generate = fake_generate

    def generate(self, **kwargs: object) -> object:
        return self._fake_generate(**kwargs)


# ---------------------------------------------------------------------------
# W3: fixture cache key includes the profile
# ---------------------------------------------------------------------------


def test_fixture_key_includes_profile() -> None:
    same_profile = _fixture_key("doc", "chunk", "interview")
    assert same_profile == _fixture_key("doc", "chunk", "interview")
    assert same_profile != _fixture_key("doc", "chunk", "generic")


def test_fixture_reads_are_profile_scoped(tmp_path: Path) -> None:
    """The same doc/chunk pair resolves to different fixture files per profile."""
    interview_key = _fixture_key("doc", "chunk", "interview")
    generic_key = _fixture_key("doc", "chunk", "generic")
    (tmp_path / f"{interview_key}.txt").write_text("interview blurb", encoding="utf-8")
    (tmp_path / f"{generic_key}.txt").write_text("generic blurb", encoding="utf-8")
    interview = ContextGenerator(
        {"ingest": {"contextualize": {"fixture_dir": str(tmp_path), "prompt_profile": "interview"}}}
    )
    generic = ContextGenerator(
        {"ingest": {"contextualize": {"fixture_dir": str(tmp_path), "prompt_profile": "generic"}}}
    )
    assert interview.generate_blurb("doc", "chunk") == "interview blurb"
    assert generic.generate_blurb("doc", "chunk") == "generic blurb"


# ---------------------------------------------------------------------------
# W3: max_context_tokens cap on the GENERATED blurb
# ---------------------------------------------------------------------------


def test_cap_blurb_passthrough_short_text() -> None:
    assert _cap_blurb("Short blurb.", 100) == "Short blurb."


def test_cap_blurb_truncates_at_sentence_boundary() -> None:
    blurb = "First sentence. Second sentence. Third sentence that is quite long and pushes."
    capped = _cap_blurb(blurb, 5)  # 20 chars
    assert capped == "First sentence."


def test_cap_blurb_truncates_at_word_boundary_without_sentences() -> None:
    blurb = "one two three four five six seven"
    capped = _cap_blurb(blurb, 4)  # 16 chars, no sentence break inside
    assert capped == "one two three"
    assert capped in blurb


def test_generated_blurb_respects_the_cap(monkeypatch) -> None:
    long_blurb = "Sentence one. " + "filler " * 60 + "Sentence two."
    gen = ContextGenerator(
        {"ingest": {"contextualize": {"live_fallback": True, "max_context_tokens": 10}}}
    )

    def _fake_generate(**_kwargs: object) -> dict[str, str]:
        return {"response": long_blurb}

    monkeypatch.setattr(gen, "_client", SimpleGenerateClient(_fake_generate))
    blurb = gen.generate_blurb("doc", "chunk")
    assert len(blurb) <= 10 * 4
    assert blurb.startswith("Sentence one.")


# ---------------------------------------------------------------------------
# W3: load-time rejection (backend / profile / apply_to / cap)
# ---------------------------------------------------------------------------


def test_default_settings_match_program_target() -> None:
    settings = load_contextual_settings({})
    assert settings == ContextualSettings(
        model="qwen3:8b",
        prompt_profile="interview",
        max_context_tokens=100,
        document_first_prompt=True,
        apply_to=["embedding", "lexical"],
        backend="ollama",
    )


@pytest.mark.parametrize(
    "block",
    [
        {"backend": "openai"},
        {"prompt_profile": "legal"},
        {"apply_to": []},
        {"apply_to": "embedding"},
        {"apply_to": ["embedding", "graph"]},
        {"max_context_tokens": 0},
        {"max_context_tokens": "50"},
        {"max_context_tokens": True},
    ],
)
def test_invalid_values_are_rejected_with_remediation(block: dict[str, object]) -> None:
    config = {"ingest": {"contextualize": block}}
    error = contextualize_settings_error(config)
    assert error is not None
    assert "ingest.contextualize" in error
    with pytest.raises(ValueError, match="ingest\\.contextualize"):
        load_contextual_settings(config)


def test_partial_block_fills_from_defaults() -> None:
    """A partial block (existing tests, hand-built dicts) must not raise."""
    settings = load_contextual_settings(
        {"ingest": {"contextualize": {"fixture_dir": str(_FIXTURE_DIR), "live_fallback": False}}}
    )
    assert settings.backend == "ollama"
    assert settings.model == "qwen3:8b"
    assert settings.fixture_dir == str(_FIXTURE_DIR)


def test_load_config_rejects_bad_contextualize_at_load(tmp_path: Path) -> None:
    """A bad backend fails the LOAD itself (config.load_config gate)."""
    import os

    from corpus_kb.config import load_config

    bad = tmp_path / "config.yaml"
    bad.write_text("ingest:\n  contextualize:\n    backend: llamacpp\n", encoding="utf-8")
    old = os.environ.get("CORPUS_KB_CONFIG")
    os.environ["CORPUS_KB_CONFIG"] = str(bad)
    try:
        with pytest.raises(ValueError, match=r"ingest\.contextualize\.backend"):
            load_config()
    finally:
        if old is None:
            os.environ.pop("CORPUS_KB_CONFIG", None)
        else:
            os.environ["CORPUS_KB_CONFIG"] = old


def test_defaults_are_three_way_synced() -> None:
    """config.py defaults, this module's constants and both YAMLs agree."""
    from corpus_kb.config import get_default_config

    repo = Path(__file__).resolve().parent.parent
    assert CONTEXTUAL_CONFIG_DEFAULTS["model"] == "qwen3:8b"
    assert CONTEXTUAL_CONFIG_DEFAULTS["max_context_tokens"] == 100
    assert CONTEXTUAL_CONFIG_DEFAULTS["prompt_profile"] == "interview"
    assert CONTEXTUAL_CONFIG_DEFAULTS["document_first_prompt"] is True
    assert CONTEXTUAL_CONFIG_DEFAULTS["apply_to"] == ["embedding", "lexical"]
    assert CONTEXTUAL_CONFIG_DEFAULTS["backend"] == "ollama"
    for rel in ("config.yaml", Path("src") / "corpus_kb" / "config.yaml"):
        data = yaml.safe_load((repo / rel).read_text(encoding="utf-8"))
        block = data["ingest"]["contextualize"]
        for key, value in CONTEXTUAL_CONFIG_DEFAULTS.items():
            assert block[key] == value, f"{rel}: {key} drifted"
    assert get_default_config()["ingest"]["contextualize"]["model"] == "qwen3:8b"
