"""PPTX structure-aware ingestion: one chunk per slide, tables as markdown.

python-pptx (a required dependency) opens the deck directly — no Unstructured
partition, which flattens slides and loses their structure. Each slide becomes
one chunk carrying the deck/slide metadata (slide_number, slide_title, layout,
author, created/modified, company, subject); table shapes render as GitHub
markdown tables; speaker notes become a trailing chunk per slide
(``include_notes``, metadata ``slide_notes``). An oversized slide body splits
at paragraph boundaries into budget-sized parts (extra ``part`` index).
Detection is suffix-based (a .pptx filename is a full claim). One unreadable
shape is a warning, never a failure; an empty deck yields an empty_reason.
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
            chunks.extend(self._slide_chunks(slide, slide_number, paragraphs, notes, meta))
            slide_texts.append("\n".join(paragraphs))
        for index, chunk in enumerate(chunks):
            chunk.index = index
        # document_text is the visible slide text; speaker notes live only in
        # their own chunks (they are speaker-side, not slide-surface content).
        return ChunkPlan(
            strategy=self.name,
            chunks=chunks,
            document_text="\n\n".join(text for text in slide_texts if text),
            empty_reason=None if chunks else "no text recovered from deck",
            warnings=warnings,
        )

    def _slide_chunks(
        self,
        slide: Slide,
        slide_number: int,
        paragraphs: list[str],
        notes: str,
        meta: DeckMeta,
    ) -> list[PlannedChunk]:
        """One slide's chunks: body parts first, then the notes chunk."""
        title = slide_title(slide)
        shared: dict[str, object] = {
            **meta.to_metadata(),
            "slide_number": slide_number,
            "slide_title": title,
            "layout": layout_name(slide),
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
        if self.include_notes and notes:
            note_lines = [line for line in notes.splitlines() if line.strip()]
            note_parts = pack_paragraphs(note_lines)
            notes_meta = {**shared, "has_notes": True, "slide_notes": notes}
            for part_index, note_text in enumerate(note_parts):
                chunks.append(
                    self._chunk(
                        note_text,
                        notes_meta,
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
