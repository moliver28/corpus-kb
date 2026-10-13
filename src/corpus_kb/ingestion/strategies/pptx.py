"""PPTX structure-aware ingestion: one chunk per slide, tables as markdown.

python-pptx (a required dependency) opens the deck directly — no Unstructured
partition, which flattens slides and loses their structure. Each slide becomes
one chunk carrying the deck/slide metadata (slide_number, slide_title, layout,
hidden, author, created/modified, company, subject); table shapes render as
GitHub markdown tables; speaker notes become a trailing chunk per slide
(``include_notes``; the full ``slide_notes`` text rides on the FIRST part
only — later split parts carry ``slide_notes_part`` instead). An oversized
slide body splits at paragraph boundaries into budget-sized parts (extra
``part`` index). Detection is suffix-based (a .pptx filename is a full
claim). One unreadable shape is a warning, never a failure; unsupported
shapes degrade loudly (charts render values, groups recurse, pictures warn);
an empty deck yields an empty_reason.

``document_text`` is exactly the text the chunks slice — body lines then the
slide's notes lines per slide, in chunk order — and every chunk carries its
exact ``(source_start_char, source_end_char)`` span into it, so late
chunking can engage for decks when the activation contract holds.
"""

from __future__ import annotations

from pptx import Presentation
from pptx.slide import Slide

from ..base import (
    ChunkPlan,
    DetectResult,
    PlannedChunk,
    SourceProfile,
)
from ._pptx_parts import (
    DeckMeta,
    deck_metadata,
    layout_name,
    pack_paragraphs,
    slide_hidden,
    slide_notes,
    slide_paragraphs,
    slide_title,
)

PPTX_STRATEGY_NAME = "pptx_structure"


class PptxStructureStrategy:
    """Slide-faithful chunking for .pptx decks (python-pptx, structure-aware)."""

    name = PPTX_STRATEGY_NAME

    def __init__(self, include_notes: bool = True) -> None:
        self.include_notes = include_notes

    def detect(self, source_type_hint: str | None, filename: str, head: str) -> DetectResult:
        """Suffix-based claim: a .pptx filename is a full claim (1.0)."""
        if (filename or "").lower().endswith(".pptx"):
            return DetectResult(1.0, "pptx-suffix")
        return DetectResult(0.0, "no pptx signal")

    def build_chunks(self, raw: str, filename: str, profile: SourceProfile) -> ChunkPlan:
        """Open the deck and emit slide chunks (+ notes chunks, + warnings)."""
        if not profile.is_file:
            return ChunkPlan(
                strategy=self.name,
                empty_reason="pptx parsing requires a file source",
            )
        try:
            deck = Presentation(profile.path)
        except Exception as exc:
            return ChunkPlan(
                strategy=self.name,
                empty_reason=f"pptx could not be opened: {exc}",
            )
        warnings: list[str] = []
        meta = deck_metadata(deck)
        chunks: list[PlannedChunk] = []
        slide_texts: list[str] = []
        for slide_number, slide in enumerate(deck.slides, start=1):
            paragraphs = slide_paragraphs(slide, slide_number, warnings)
            notes = slide_notes(slide, slide_number, warnings)
            note_lines = _note_lines(notes)
            chunks.extend(
                self._slide_chunks(slide, slide_number, paragraphs, notes, note_lines, meta)
            )
            # document_text slices: body lines, then the notes lines when the
            # caller wants notes chunks (the only text those chunks slice).
            lines = [*paragraphs, *note_lines] if self.include_notes else paragraphs
            slide_texts.append("\n".join(lines))
        for index, chunk in enumerate(chunks):
            chunk.index = index
        document_text = "\n\n".join(text for text in slide_texts if text)
        _locate_spans(chunks, document_text)
        return ChunkPlan(
            strategy=self.name,
            chunks=chunks,
            document_text=document_text,
            empty_reason=None if chunks else "no text recovered from deck",
            warnings=warnings,
        )

    def _slide_chunks(
        self,
        slide: Slide,
        slide_number: int,
        paragraphs: list[str],
        notes: str,
        note_lines: list[str],
        meta: DeckMeta,
    ) -> list[PlannedChunk]:
        """One slide's chunks: body parts first, then the notes parts."""
        title = slide_title(slide)
        shared: dict[str, object] = {
            **meta.to_metadata(),
            "slide_number": slide_number,
            "slide_title": title,
            "layout": layout_name(slide),
            "hidden": slide_hidden(slide),
            "has_notes": bool(notes),
        }
        heading = [value for value in (meta.deck_title, title) if value]
        heading_path: list[str] | None = heading or None
        chunks: list[PlannedChunk] = []
        bodies = pack_paragraphs(paragraphs)
        part_count = len(bodies)
        for part_index, body in enumerate(bodies):
            chunks.append(
                self._chunk(
                    body,
                    shared,
                    part_index if part_count > 1 else None,
                    heading_path,
                    slide_number,
                )
            )
        if self.include_notes and note_lines:
            note_parts = pack_paragraphs(note_lines)
            for part_index, note_text in enumerate(note_parts):
                chunks.append(
                    self._chunk(
                        note_text,
                        _notes_meta(shared, notes, part_index),
                        part_index if len(note_parts) > 1 else None,
                        heading_path,
                        slide_number,
                    )
                )
        return chunks

    def _chunk(
        self,
        text: str,
        metadata: dict[str, object],
        part: int | None,
        heading_path: list[str] | None,
        slide_number: int,
    ) -> PlannedChunk:
        """One PlannedChunk (index renumbered 0..n-1 in build_chunks)."""
        metadata = dict(metadata)
        if part is not None:
            metadata["part"] = part
        return PlannedChunk(
            text=text,
            index=0,
            metadata=metadata,
            source_type="text",
            heading_path=heading_path,
            # pseudo lines: one slide == one line position in the deck
            start_line=slide_number,
            end_line=slide_number,
        )


def _note_lines(notes: str) -> list[str]:
    """The notes' non-blank lines — exactly the lines notes chunks are built from."""
    return [line for line in notes.splitlines() if line.strip()]


def _notes_meta(shared: dict[str, object], notes: str, part_index: int) -> dict[str, object]:
    """Notes-part metadata: the full notes string rides on the FIRST part only.

    Later parts of a split notes chunk carry ``slide_notes_part`` instead of
    duplicating kilobytes of notes text per part.
    """
    metadata: dict[str, object] = {**shared, "has_notes": True}
    if part_index == 0:
        metadata["slide_notes"] = notes
    else:
        metadata["slide_notes_part"] = part_index
    return metadata


def _locate_spans(chunks: list[PlannedChunk], document_text: str) -> None:
    """Locate each chunk's exact span in document_text (find-with-cursor).

    House pattern (chunking.unstructured_chunker): search from the previous
    chunk's end, retry the stripped text on a miss. A chunk that still cannot
    be found keeps ``None`` spans — honest absence, never a fabricated offset.
    """
    cursor = 0
    for chunk in chunks:
        start = document_text.find(chunk.text, cursor)
        if start == -1:
            stripped = chunk.text.strip()
            if stripped and stripped != chunk.text:
                start = document_text.find(stripped, cursor)
                if start != -1:
                    chunk.text = stripped
        if start == -1:
            continue
        chunk.source_start_char = start
        chunk.source_end_char = start + len(chunk.text)
        cursor = start + len(chunk.text)
