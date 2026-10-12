"""U48/P6: path adapters feeding the hierarchy builder on BOTH ingest paths.

* ``segments_from_chunks`` — MAIN RAG path: the existing Unstructured
  elements (already mapped to ``Chunk`` records by
  ``chunking/unstructured_chunker.py``) become the atomic segments. Heading
  elements are tagged ``kind="heading"``; consecutive elements under the
  same heading path share a ``group_id`` so the chunk/parent levels snap to
  heading boundaries when ``heading`` is in ``respect_boundaries``.
* ``segments_from_turns`` — RESEARCH-GRAIN path: speaker turns (the coding
  unit, ``research/chunking.py`` taxonomy) become the atomic segments,
  tagged ``kind="speaker_turn"``. When an exchange mapping is supplied and
  ``qa_pair`` is in ``respect_boundaries``, turns of one exchange share a
  ``group_id`` so mid-level windows snap to exchange (qa_pair) boundaries.

Char offsets: the main path carries source offsets on the Chunk records;
when absent (raw-text ingest leaves them unset) offsets are derived by
sequential ``str.find`` over the document text — the same convention as
``unstructured_chunker.chunk_elements``. The research path synthesizes
offsets over the concatenated turn text.
"""

from __future__ import annotations

from typing import Protocol

from ..utils.models import Chunk
from .hierarchy import Segment

HEADING_ELEMENTS = frozenset({"Title", "Header"})


def _offsets_for(original_text: str, chunks: list[Chunk]) -> list[tuple[int, int]]:
    """Resolve (start, end) source offsets, deriving the missing ones."""
    offsets: list[tuple[int, int]] = []
    cursor = 0
    for chunk in chunks:
        start = chunk.source_start_char
        end = chunk.source_end_char
        if start is None or end is None:
            found = original_text.find(chunk.text, cursor)
            if found == -1:
                found = cursor
            start = found
            end = found + len(chunk.text)
        offsets.append((start, end))
        cursor = end
    return offsets


def segments_from_chunks(
    chunks: list[Chunk],
    original_text: str | None = None,
) -> list[Segment]:
    """Main-RAG-path segments: one per v1 element/chunk, heading-grouped."""
    if original_text is None:
        original_text = "".join(chunk.text for chunk in chunks)
    offsets = _offsets_for(original_text, chunks)
    segments: list[Segment] = []
    for chunk, (start, end) in zip(chunks, offsets, strict=True):
        element_type = str(chunk.metadata.get("element_type", ""))
        heading_path = chunk.heading_path or []
        is_heading = element_type in HEADING_ELEMENTS
        is_symbol = chunk.entity_type is not None  # code symbol unit (AST path)
        group_id = "|".join(heading_path) if heading_path else None
        segments.append(
            Segment(
                text=chunk.text,
                start_char=start,
                end_char=end,
                kind="heading" if is_heading else ("symbol" if is_symbol else "paragraph"),
                group_id=group_id,
            )
        )
    return segments


class TurnLike(Protocol):
    """Structural minimum for a research speaker turn (duck-typed).

    Matches ``domain.transcript.TurnSpec`` and ``research_units`` rows:
    ``text`` plus an optional ``seq`` for stable ordering.
    """

    @property
    def text(self) -> str: ...

    @property
    def seq(self) -> int | None: ...


def segments_from_turns(
    turns: list[TurnLike],
    exchange_of: dict[int, int] | None = None,
) -> list[Segment]:
    """Research-path segments: speaker turns, optionally exchange-grouped.

    ``exchange_of`` maps turn seq -> exchange id; when provided, turns of
    one exchange share a ``group_id`` so the chunk/parent levels snap to
    qa_pair boundaries (the packer merges them only when ``qa_pair`` is in
    ``respect_boundaries``).
    """
    segments: list[Segment] = []
    cursor = 0
    for turn in turns:
        text = turn.text
        seq = turn.seq
        group_id = str(exchange_of[seq]) if exchange_of and seq in exchange_of else None
        segments.append(
            Segment(
                text=text,
                start_char=cursor,
                end_char=cursor + len(text),
                kind="speaker_turn",
                group_id=group_id,
            )
        )
        cursor += len(text)
    return segments
