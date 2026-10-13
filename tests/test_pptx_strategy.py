"""PPTX structure strategy: slide chunks, tables, notes, budget split, registry.

The complex deck is built in-test via tests/pptx_factory.py (python-pptx) —
no binary fixture is committed. Offline suite: detection verdicts, chunk
boundaries, slide/deck metadata, table markdown, oversized-slide splitting,
validate_plan conformance, empty-reason paths, and registry resolution.
"""

from __future__ import annotations

from pathlib import Path

from pptx_factory import (
    DECK_AUTHOR,
    DECK_COMPANY,
    DECK_CREATED,
    DECK_MODIFIED,
    DECK_SUBJECT,
    DECK_TITLE,
    NOTED_SLIDES,
    SLIDE_COUNT,
    TABLE_HEADER,
    TABLE_ROWS,
    TABLE_TITLE,
    make_blank_deck,
    make_oversized_deck,
    make_research_deck,
)

from corpus_kb.ingestion.base import (
    TOKEN_BUDGET_CHARS,
    SourceProfile,
    validate_plan,
)
from corpus_kb.ingestion.registry import resolve_strategy
from corpus_kb.ingestion.strategies.pptx import PPTX_STRATEGY_NAME, PptxStructureStrategy

_STRATEGY_KEYS = {
    "slide_number",
    "slide_count",
    "deck_title",
    "slide_title",
    "layout",
    "author",
    "created",
    "modified",
    "company",
    "subject",
    "has_notes",
}


def _plan_for(deck_path: Path, include_notes: bool = True):
    strategy = PptxStructureStrategy(include_notes=include_notes)
    return strategy.build_chunks(
        "", "deck.pptx", SourceProfile(source_type="pptx", is_file=True, path=str(deck_path))
    )


def _body_chunks(plan):
    return [chunk for chunk in plan.chunks if "slide_notes" not in chunk.metadata]


def _notes_chunks(plan):
    return [chunk for chunk in plan.chunks if "slide_notes" in chunk.metadata]


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_detect_pptx_suffix_is_full_claim() -> None:
    result = PptxStructureStrategy().detect(None, "survey.pptx", "")
    assert result.confidence == 1.0
    assert result.reason == "pptx-suffix"


def test_detect_non_pptx_filename_is_silent() -> None:
    result = PptxStructureStrategy().detect("pptx", "survey.txt", "anything")
    assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# Chunk boundaries + metadata
# ---------------------------------------------------------------------------


def test_chunk_boundaries_match_slide_count_plus_notes(tmp_path: Path) -> None:
    plan = _plan_for(make_research_deck(tmp_path / "survey.pptx"))
    assert plan.empty_reason is None
    body_slides = {chunk.metadata["slide_number"] for chunk in _body_chunks(plan)}
    assert body_slides == set(range(1, SLIDE_COUNT + 1))
    assert len(_notes_chunks(plan)) == len(NOTED_SLIDES)
    assert len(plan.chunks) == SLIDE_COUNT + len(NOTED_SLIDES)
    assert [chunk.index for chunk in plan.chunks] == list(range(len(plan.chunks)))


def test_slide_metadata_carries_core_and_extended_properties(tmp_path: Path) -> None:
    plan = _plan_for(make_research_deck(tmp_path / "survey.pptx"))
    first = plan.chunks[0]
    meta = first.metadata
    assert set(meta) == _STRATEGY_KEYS
    assert meta["slide_number"] == 1
    assert meta["slide_count"] == SLIDE_COUNT
    assert meta["deck_title"] == DECK_TITLE
    assert meta["author"] == DECK_AUTHOR
    assert meta["created"] == DECK_CREATED.isoformat()
    assert meta["modified"] == DECK_MODIFIED.isoformat()
    assert meta["company"] == DECK_COMPANY
    assert meta["subject"] == DECK_SUBJECT
    assert meta["slide_title"] == DECK_TITLE  # title slide repeats the deck title
    assert meta["layout"] == "Title Slide"
    assert meta["has_notes"] is True
    # TITLE breadcrumb carried on every slide chunk (skip-None join)
    assert first.heading_path == [DECK_TITLE, DECK_TITLE]
    assert (first.start_line, first.end_line) == (1, 1)
    later = next(c for c in _body_chunks(plan) if c.metadata["slide_number"] == 4)
    assert later.metadata["slide_title"] == TABLE_TITLE
    assert later.heading_path == [DECK_TITLE, TABLE_TITLE]


def test_table_slide_renders_github_markdown(tmp_path: Path) -> None:
    plan = _plan_for(make_research_deck(tmp_path / "survey.pptx"))
    table_chunk = next(
        chunk for chunk in _body_chunks(plan) if chunk.metadata["slide_title"] == TABLE_TITLE
    )
    header = "| " + " | ".join(TABLE_HEADER) + " |"
    assert header in table_chunk.text
    assert "| --- | --- | --- |" in table_chunk.text
    for row in TABLE_ROWS:
        assert "| " + " | ".join(row) + " |" in table_chunk.text


def test_notes_chunks_carry_notes_text_and_flag(tmp_path: Path) -> None:
    plan = _plan_for(make_research_deck(tmp_path / "survey.pptx"))
    notes_chunks = _notes_chunks(plan)
    assert {chunk.metadata["slide_number"] for chunk in notes_chunks} == NOTED_SLIDES
    for chunk in notes_chunks:
        assert chunk.metadata["has_notes"] is True
        assert chunk.metadata["slide_notes"] == chunk.text
    welcome = next(chunk for chunk in notes_chunks if chunk.metadata["slide_number"] == 1)
    assert welcome.text.startswith("Welcome everyone")
    # body chunks on note-less slides report has_notes honestly
    clean = next(chunk for chunk in _body_chunks(plan) if chunk.metadata["slide_number"] == 4)
    assert clean.metadata["has_notes"] is False


def test_include_notes_false_excludes_notes_chunks(tmp_path: Path) -> None:
    plan = _plan_for(make_research_deck(tmp_path / "survey.pptx"), include_notes=False)
    assert len(plan.chunks) == SLIDE_COUNT
    assert all("slide_notes" not in chunk.metadata for chunk in plan.chunks)
    noted = {chunk.metadata["slide_number"] for chunk in plan.chunks if chunk.metadata["has_notes"]}
    assert noted == NOTED_SLIDES  # presence is still recorded on the body chunk


# ---------------------------------------------------------------------------
# Budget conformance: whole deck + oversized-slide splitting
# ---------------------------------------------------------------------------


def test_whole_research_deck_passes_validate_plan(tmp_path: Path) -> None:
    plan = _plan_for(make_research_deck(tmp_path / "survey.pptx"))
    assert validate_plan(plan) == []
    assert plan.document_text  # title slide's text anchors the document text
    assert DECK_TITLE in plan.document_text
    assert all("qa_context" not in chunk.metadata for chunk in plan.chunks)


def test_oversized_slide_splits_with_part_indices(tmp_path: Path) -> None:
    plan = _plan_for(make_oversized_deck(tmp_path / "big.pptx"))
    bodies = _body_chunks(plan)
    assert len(bodies) > 1
    assert [chunk.metadata["part"] for chunk in bodies] == list(range(len(bodies)))
    assert all(len(chunk.text) <= TOKEN_BUDGET_CHARS for chunk in bodies)
    assert all(chunk.metadata["slide_number"] == 1 for chunk in bodies)
    joined = "\n".join(chunk.text for chunk in bodies)
    assert "gap analysis 0" in joined and "gap analysis 4" in joined
    assert "overflow paragraph" in joined  # hard-split tail survived
    assert validate_plan(plan) == []


# ---------------------------------------------------------------------------
# Empty / degenerate plans
# ---------------------------------------------------------------------------


def test_empty_deck_yields_empty_reason(tmp_path: Path) -> None:
    plan = _plan_for(make_blank_deck(tmp_path / "blank.pptx"))
    assert plan.chunks == []
    assert plan.empty_reason == "no text recovered from deck"
    assert validate_plan(plan) == []


def test_non_file_source_yields_empty_reason() -> None:
    plan = PptxStructureStrategy().build_chunks(
        "raw", "x.pptx", SourceProfile(source_type="pptx", is_file=False, path="x.pptx")
    )
    assert plan.chunks == []
    assert plan.empty_reason == "pptx parsing requires a file source"
    assert validate_plan(plan) == []


def test_corrupt_file_yields_empty_reason(tmp_path: Path) -> None:
    bad = tmp_path / "bad.pptx"
    bad.write_bytes(b"not a zip archive")
    plan = _plan_for(bad)
    assert plan.chunks == []
    assert plan.empty_reason is not None and "could not be opened" in plan.empty_reason
    assert validate_plan(plan) == []


# ---------------------------------------------------------------------------
# Registry resolution
# ---------------------------------------------------------------------------


def test_registry_resolves_pptx_via_config_mapping() -> None:
    config: dict[str, object] = {"ingestion": {"strategies": {"pptx": "pptx_structure"}}}
    resolution = resolve_strategy("pptx", "survey.pptx", "", config)
    assert resolution.strategy.name == PPTX_STRATEGY_NAME
    assert resolution.basis == "config"
    assert resolution.detected == PPTX_STRATEGY_NAME  # 1.0 claim always runs


def test_detection_adopts_pptx_suffix_without_mapping() -> None:
    resolution = resolve_strategy("text", "survey.pptx", "", {"ingestion": {}})
    assert resolution.strategy.name == PPTX_STRATEGY_NAME
    assert resolution.basis == "detection"
    assert resolution.detection is not None
    assert resolution.detection.reason == "pptx-suffix"
