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
