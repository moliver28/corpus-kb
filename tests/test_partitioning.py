"""Tests for the Unstructured partitioning layer and element chunker."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.chunking.unstructured_chunker import chunk_elements
from src.partitioning import partition

_FIXTURES = Path(__file__).parent / "fixtures"


def test_partition_markdown_auto_round_trip() -> None:
    """Partitioning a markdown fixture yields chunks with exact char offsets."""
    path = _FIXTURES / "ontology_sample.md"
    original_text = path.read_text(encoding="utf-8")

    elements = partition(path, strategy="auto")
    chunks = chunk_elements(elements, original_text, document_id="doc-md")

    assert len(chunks) > 0
    for chunk in chunks:
        assert chunk.source_start_char is not None
        assert chunk.source_end_char is not None
        assert chunk.source_start_char < chunk.source_end_char
        assert len(chunk.text) > 0
        assert (
            original_text[chunk.source_start_char : chunk.source_end_char] == chunk.text
        )
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
