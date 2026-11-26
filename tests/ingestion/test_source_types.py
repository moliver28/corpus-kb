"""Typed source_types registry: canonical names, aliases, and the text gate."""

from __future__ import annotations


def test_canonical_names_resolve():
    from corpus_kb.ingestion.source_types import resolve_source_type

    spec = resolve_source_type("participant-session")
    assert spec is not None and spec.strategy == "transcript"
    assert resolve_source_type("internal-meeting") is not None
    assert resolve_source_type("external-site") is not None


def test_aliases_map_to_canonical():
    from corpus_kb.ingestion.source_types import canonical_source_type

    assert canonical_source_type("transcript") == "participant-session"
    assert canonical_source_type("web") == "external-site"


def test_canonical_source_type_passthrough_preserves_stored_values():
    from corpus_kb.ingestion.source_types import canonical_source_type

    assert canonical_source_type("text") == "text"
    assert canonical_source_type("code") == "code"
    assert canonical_source_type("markdown") == "markdown"
    assert canonical_source_type("pptx_structure") == "pptx_structure"  # strategy override


def test_ingest_text_acceptance_gate():
    from corpus_kb.ingestion.source_types import is_accepted_ingest_text_type

    assert is_accepted_ingest_text_type("participant-session")
    assert is_accepted_ingest_text_type("transcript")
    assert is_accepted_ingest_text_type("internal-meeting")
    assert is_accepted_ingest_text_type("text")
    assert not is_accepted_ingest_text_type("external-site")
    assert not is_accepted_ingest_text_type("web")
    assert not is_accepted_ingest_text_type("nonsense")


def test_entry_point_group_constant():
    from corpus_kb.ingestion.source_types import ENTRY_POINT_GROUP

    assert ENTRY_POINT_GROUP == "corpus_kb.source_types"


# ---------------------------------------------------------------------------
# Registry fallback (the source_types spec feeds strategy resolution)
# ---------------------------------------------------------------------------


_PROSE = "Plain prose, no headings and no speaker turns."


def test_registry_fallback_canonical_types_get_declared_strategy():
    from corpus_kb.ingestion.registry import resolve_strategy

    resolution = resolve_strategy("participant-session", "session.txt", _PROSE, {})
    assert resolution.strategy.name == "transcript"
    assert resolution.basis == "registry"
    assert resolution.mapped_source_type == "participant-session"

    resolution = resolve_strategy("internal-meeting", "notes.txt", _PROSE, {})
    assert resolution.strategy.name == "markdown_sections"
    assert resolution.basis == "registry"


def test_registry_fallback_covers_aliases():
    from corpus_kb.ingestion.registry import resolve_strategy

    # The transcript alias carries a weak detection hint (0.2 < the 0.5
    # adopt threshold), so with no other signal the registry decides.
    resolution = resolve_strategy("transcript", "call.txt", _PROSE, {})
    assert resolution.strategy.name == "transcript"
    assert resolution.basis == "registry"


def test_config_map_still_beats_registry_fallback():
    from corpus_kb.ingestion.registry import resolve_strategy

    config = {"ingestion": {"strategies": {"participant-session": "default"}}}
    resolution = resolve_strategy("participant-session", "session.txt", _PROSE, config)
    assert resolution.strategy.name == "default"
    assert resolution.basis == "config"


def test_confident_detection_beats_registry_fallback():
    from corpus_kb.ingestion.registry import resolve_strategy

    # A moderator-pattern head claims transcript at the adopt threshold, so
    # detection wins even though the spec declares markdown_sections.
    head = (
        "Moderator: Welcome everyone.\n"
        "P1: Glad to be here.\n"
        "P2: Thanks for having us.\n"
        "Interviewer: Let us begin.\n"
    )
    resolution = resolve_strategy("internal-meeting", "notes.txt", head, {})
    assert resolution.strategy.name == "transcript"
    assert resolution.basis == "detection"


def test_generic_and_unknown_names_skip_the_fallback():
    from corpus_kb.ingestion.registry import resolve_strategy

    resolution = resolve_strategy("text", "notes.txt", _PROSE, {})
    assert resolution.basis == "default"
    resolution = resolve_strategy("mystery-type", "notes.txt", _PROSE, {})
    assert resolution.basis == "default"
