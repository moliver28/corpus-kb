"""U48/P6: path adapters feeding the hierarchy builder on BOTH ingest paths.

* ``segments_from_chunks`` — MAIN RAG path: the existing Unstructured
  elements (already mapped to ``Chunk`` records by
  ``chunking/unstructured_chunker.py``) become the atomic segments. Heading
  elements are tagged ``kind="heading"``; consecutive elements under the
  same heading path share a ``group_id`` so the chunk/parent levels snap to
  heading boundaries when ``heading`` is in ``respect_boundaries``.
* ``segments_from_slides`` — DECK path: pptx-strategy chunks (one per slide
  part, ``ingestion/strategies/pptx.py``) grouped per ``slide_number`` into
  ``kind="slide"`` segments so the chunk/parent levels snap to slide
  boundaries when ``slide`` is in ``respect_boundaries``.
* ``segments_from_turns`` — RESEARCH-GRAIN path: speaker turns (the coding
  unit, ``research/chunking.py`` taxonomy) become the atomic segments,
  tagged ``kind="speaker_turn"``. When an exchange mapping is supplied and
  ``qa_pair`` is in ``respect_boundaries``, turns of one exchange share a
  ``group_id`` so mid-level windows snap to exchange (qa_pair) boundaries.
* ``segments_from_transcript_chunks`` — TRANSCRIPT-strategy bridge: the
  pipeline's transcript chunks (already turn-aligned by the strategy)
  feed ``segments_from_turns``, with exchange groups read from each
  chunk's ``qa_context`` metadata.

Char offsets: the main path carries source offsets on the Chunk records;
when absent (raw-text ingest leaves them unset) offsets are derived by
sequential ``str.find`` over the document text — the same convention as
``unstructured_chunker.chunk_elements``. The research path synthesizes
offsets over the concatenated turn text.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from ..ingestion.base import QA_CONTEXT_KEY
from ..utils.models import Chunk
from .hierarchy import Segment

HEADING_ELEMENTS = frozenset({"Title", "Header"})

# qa_context exchange ids carry a "-partK" suffix on budget-split parts;
# parts of one exchange are one qa_pair boundary, so the suffix is stripped
# before grouping (the packer re-splits oversized groups).
_QA_PART_SUFFIX = re.compile(r"-part\d+$")


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


def segments_from_slides(
    chunks: list[Chunk],
    original_text: str | None = None,
) -> list[Segment]:
    """Deck-path segments: pptx chunks grouped per slide, ``kind="slide"``.

    Chunks carrying an integer ``slide_number`` share that slide's
    ``group_id`` so the chunk/parent levels snap to slide boundaries (the
    slide_title breadcrumb stays on the chunk's ``heading_path``; grouping
    keys on slide_number, which stays unique when titles repeat or vanish).
    Unnumbered chunks (notes-only or malformed decks) fall through to the
    heading grouping of ``segments_from_chunks``.
    """
    if original_text is None:
        original_text = "".join(chunk.text for chunk in chunks)
    offsets = _offsets_for(original_text, chunks)
    segments: list[Segment] = []
    for chunk, (start, end) in zip(chunks, offsets, strict=True):
        slide_number = chunk.metadata.get("slide_number")
        is_numbered = isinstance(slide_number, int) and not isinstance(slide_number, bool)
        if is_numbered:
            segments.append(
                Segment(
                    text=chunk.text,
                    start_char=start,
                    end_char=end,
                    kind="slide",
                    group_id=f"slide-{slide_number}",
                )
            )
            continue
        element_type = str(chunk.metadata.get("element_type", ""))
        heading_path = chunk.heading_path or []
        is_heading = element_type in HEADING_ELEMENTS
        group_id = "|".join(heading_path) if heading_path else None
        segments.append(
            Segment(
                text=chunk.text,
                start_char=start,
                end_char=end,
                kind="heading" if is_heading else "paragraph",
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


@dataclass(frozen=True)
class IndexedTurn:
    """One pipeline chunk lifted to the turn shape (text + position seq)."""

    seq: int
    text: str


def segments_from_turns(
    turns: Sequence[TurnLike],
    exchange_of: dict[int, int | str] | None = None,
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


def segments_from_transcript_chunks(chunks: list[Chunk]) -> list[Segment]:
    """Transcript-strategy segments: one speaker-turn segment per chunk.

    Each turn-aligned chunk (one Q+A exchange part or one meeting window)
    becomes an atomic ``speaker_turn`` segment. Chunks carrying
    ``qa_context`` share their exchange's ``group_id`` — the ``-partK``
    suffix stripped, so budget-split parts of one exchange regroup — and
    window chunks without ``qa_context`` stay ungrouped. Offsets follow the
    research-path convention: synthesized over the concatenated chunk text.
    """
    turns: list[IndexedTurn] = []
    exchange_of: dict[int, int | str] = {}
    for index, chunk in enumerate(chunks):
        turns.append(IndexedTurn(seq=index, text=chunk.text))
        qa_context = chunk.metadata.get(QA_CONTEXT_KEY)
        if isinstance(qa_context, dict):
            exchange_id = qa_context.get("exchange_id")
            if isinstance(exchange_id, str):
                exchange_of[index] = _QA_PART_SUFFIX.sub("", exchange_id)
    return segments_from_turns(turns, exchange_of or None)
