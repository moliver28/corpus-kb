"""Tests for the Unstructured partitioning layer and element chunker."""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pytest

from corpus_kb.chunking.unstructured_chunker import MAX_CHUNK_CHARS, chunk_elements
from corpus_kb.partitioning import ElementProxy, partition

_FIXTURES = Path(__file__).parent / "fixtures"


def test_partition_markdown_auto_round_trip() -> None:
    """Partitioning a markdown fixture yields chunks with exact char offsets."""
    path = _FIXTURES / "ontology_sample.md"
    original_text = path.read_text(encoding="utf-8")

    try:
        elements = partition(path, strategy="auto")
    except ImportError as exc:
        if "partition_md" not in str(exc):
            raise
        pytest.skip("partition_md not available")
    chunks = chunk_elements(elements, original_text, document_id="doc-md")

    assert len(chunks) > 0
    for chunk in chunks:
        assert chunk.source_start_char is not None
        assert chunk.source_end_char is not None
        assert chunk.source_start_char < chunk.source_end_char
        assert len(chunk.text) > 0
        assert original_text[chunk.source_start_char : chunk.source_end_char] == chunk.text
        assert chunk.document_id == "doc-md"
        assert "element_type" in chunk.metadata
        assert "parent_id" in chunk.metadata
        assert "heading_path" in chunk.metadata


def test_partition_text_fast() -> None:
    """Fast partitioning of a plain text fixture returns non-empty chunks."""
    path = _FIXTURES / "sample.txt"
    original_text = path.read_text(encoding="utf-8")

    elements = partition(path, strategy="fast")
    chunks = chunk_elements(elements, original_text, document_id="doc-txt")

    assert len(chunks) >= 1
    for chunk in chunks:
        assert len(chunk.text) > 0


def test_partition_missing_file() -> None:
    """Partitioning a non-existent path raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        partition("nonexistent.md")


@pytest.mark.requires_hi_res
def test_partition_pdf_hi_res() -> None:
    """Hi-res partitioning of a small PDF returns non-empty, valid chunks."""
    path = _FIXTURES / "sample.pdf"

    elements = partition(path, strategy="hi_res")
    original_text = "".join(element.text for element in elements)
    chunks = chunk_elements(elements, original_text, document_id="doc-pdf")

    assert len(chunks) >= 1
    for chunk in chunks:
        assert len(chunk.text) > 0
        assert chunk.source_start_char is not None
        assert chunk.source_end_char is not None
        assert chunk.source_start_char < chunk.source_end_char


def test_chunk_elements_splits_oversized_element() -> None:
    """One element over the plan budget splits; slices stay exact and ordered."""
    # No newlines at all: the splitter must fall through to whitespace cuts.
    text = ("alpha beta gamma delta " * 2500).strip()  # ~55k chars, one element
    elements = [ElementProxy(text=text, element_type="NarrativeText", element_id="big")]
    chunks = chunk_elements(elements, text, document_id="d")

    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk.text) <= MAX_CHUNK_CHARS
        assert chunk.metadata["element_type"] == "NarrativeText"
    assert "".join(chunk.text for chunk in chunks) == text
    for prev, nxt in pairwise(chunks):
        assert nxt.source_start_char == prev.source_end_char
        assert nxt.start_line >= prev.end_line


def test_chunk_elements_oversized_prefers_paragraph_cuts() -> None:
    """A giant multi-paragraph element splits at blank lines, not mid-word."""
    para = "Sentence one. Sentence two. Sentence three.\n\n"
    text = para * 300  # ~13.5k chars of clean paragraphs
    elements = [ElementProxy(text=text, element_type="NarrativeText", element_id="big")]
    chunks = chunk_elements(elements, text, document_id="d")

    assert len(chunks) > 1
    assert "".join(chunk.text for chunk in chunks) == text
    for chunk in chunks:
        assert len(chunk.text) <= MAX_CHUNK_CHARS
        assert chunk.text.rstrip().endswith("Sentence three.")
