"""Canonical IngestDocument span tests (unified-ingestion Wave 2).

The offset invariant is the contract: a span is reported only when
``text[start_char:end_char] == artifact_text``. No envelope key or stored
column changes; these tests pin the read-only helpers.
"""

from __future__ import annotations

from corpus_kb.tools.pipeline.document import ArtifactSpan, IngestDocument
from corpus_kb.utils.models import Chunk, Document, Relation


def _doc(text: str, chunks: list[Chunk]) -> IngestDocument:
    return IngestDocument(
        document=Document(path="p", source_type="text", content=text, size_bytes=len(text)),
        text=text,
        file_hash="0" * 64,
        source_type="text",
        path="p",
        is_file=False,
        chunks=chunks,
    )


def test_chunk_span_holds_when_offsets_slice_to_text() -> None:
    chunks = [
        Chunk(
            document_id="d1",
            text="hello",
            source_type="text",
            source_start_char=0,
            source_end_char=5,
        )
    ]
    canonical = _doc("hello world", chunks)
    assert canonical.chunk_span(chunks[0]) == ArtifactSpan("d1", 0, 5)
    assert canonical.spans()[str(chunks[0].chunk_id)] == ArtifactSpan("d1", 0, 5)


def test_chunk_span_is_none_when_offsets_do_not_slice_to_text() -> None:
    chunks = [
        Chunk(
            document_id="d1",
            text="world",
            source_type="text",
            source_start_char=0,
            source_end_char=5,
        )
    ]
    canonical = _doc("hello world", chunks)
    assert canonical.chunk_span(chunks[0]) is None
    assert canonical.offset_invariant_problems() == [str(chunks[0].chunk_id)]


def test_chunk_span_is_none_when_offsets_unset() -> None:
    chunks = [Chunk(document_id="d1", text="hello", source_type="text")]
    canonical = _doc("hello world", chunks)
    assert canonical.chunk_span(chunks[0]) is None
    assert canonical.offset_invariant_problems() == []


def test_anchored_span_resolves_the_artifacts_chunk() -> None:
    chunks = [
        Chunk(
            document_id="d1",
            text="hello",
            source_type="text",
            source_start_char=4,
            source_end_char=9,
        )
    ]
    canonical = _doc("say hello", chunks)
    chunks[0].document_id = "d1"
    relation = Relation(
        source_entity_id="a",
        target_entity_id="b",
        relation_type="REFERENCES",
        chunk_id=chunks[0].chunk_id,
    )
    assert canonical.anchored_span(relation) == ArtifactSpan("d1", 4, 9)
    relation.chunk_id = None
    assert canonical.anchored_span(relation) is None
