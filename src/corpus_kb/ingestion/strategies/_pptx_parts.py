"""PPTX part renderers: shape paragraphs, GitHub tables, budget packing.

Pure helpers over python-pptx objects so :mod:`corpus_kb.ingestion.strategies.pptx`
stays an orchestration module (250-line limit). Every helper is total: one
unreadable shape appends a warning and is skipped — a deck is ingested with
whatever text it yields, never a crash for one bad shape.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime

from pptx.presentation import Presentation
from pptx.shapes.autoshape import Shape
from pptx.shapes.graphfrm import GraphicFrame
from pptx.slide import Slide
from pptx.table import Table

from ..base import TOKEN_BUDGET_CHARS

# ``Company`` lives in docProps/app.xml (extended properties), NOT in
# docProps/core.xml: python-pptx exposes the latter via core_properties and
# has no API for the former, so it is read from the package part directly.
_EXTENDED_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}"
_APP_XML_PART = "/docProps/app.xml"


@dataclass(frozen=True)
class DeckMeta:
    """Deck-level properties shared by every chunk (all JSON-safe)."""

    slide_count: int
    deck_title: str | None
    author: str | None
    created: str | None
    modified: str | None
    company: str | None
    subject: str | None

    def to_metadata(self) -> dict[str, object]:
        """The metadata dict every slide chunk starts from."""
        return {
            "slide_count": self.slide_count,
            "deck_title": self.deck_title,
            "author": self.author,
            "created": self.created,
            "modified": self.modified,
            "company": self.company,
            "subject": self.subject,
        }


def deck_metadata(deck: Presentation) -> DeckMeta:
    """Read the deck's core/extended properties (None when unset)."""
    props = deck.core_properties
    return DeckMeta(
        slide_count=len(deck.slides),
        deck_title=_clean_text(props.title),
        author=_clean_text(props.author),
        created=_isoformat(props.created),
        modified=_isoformat(props.modified),
        company=_company(deck),
        subject=_clean_text(props.subject),
    )


def slide_paragraphs(slide: Slide, slide_number: int, warnings: list[str]) -> list[str]:
    """Shape text + table markdown lines in reading order (shape order).

    The title placeholder is included like any other text shape — on a title
    slide it is the slide's whole body. One unreadable shape appends a
    warning and is skipped.
    """
    paragraphs: list[str] = []
    for shape in slide.shapes:
        try:
            if isinstance(shape, GraphicFrame) and shape.has_table:
                paragraphs.extend(table_markdown_lines(shape.table))
            elif isinstance(shape, Shape):
                for paragraph in shape.text_frame.paragraphs:
                    text = paragraph.text.strip()
                    if text:
                        paragraphs.append(text)
        except Exception as exc:
            warnings.append(f"slide {slide_number}: unreadable shape skipped: {exc}")
    return paragraphs


def slide_title(slide: Slide) -> str | None:
    """The slide's title-placeholder text, or None when absent/empty."""
    title = slide.shapes.title
    if title is None or not title.has_text_frame:
        return None
    return _clean_text(title.text_frame.text)


def layout_name(slide: Slide) -> str | None:
    """The slide layout's name (e.g. "Title and Content"), or None."""
    return _clean_text(slide.slide_layout.name)


def slide_notes(slide: Slide, slide_number: int, warnings: list[str]) -> str:
    """Speaker-notes text, or "" when the slide has none (never raises)."""
    try:
        if not slide.has_notes_slide:
            return ""
        notes_frame = slide.notes_slide.notes_text_frame
        text = notes_frame.text if notes_frame is not None else ""
    except Exception as exc:
        warnings.append(f"slide {slide_number}: unreadable notes skipped: {exc}")
        return ""
    return text.strip()


def table_markdown_lines(table: Table) -> list[str]:
    """Render a table as GitHub-markdown lines (header, separator, rows).

    Cell text is whitespace-collapsed so every row stays one line.
    """
    # explicit loop, not list(): _RowCollection iterates via __getitem__
    rows = []
    for row in table.rows:
        rows.append(row)
    if not rows:
        return []
    header = [_cell_text(cell) for cell in rows[0].cells]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * len(header)) + " |"]
    for row in rows[1:]:
        lines.append("| " + " | ".join(_cell_text(cell) for cell in row.cells) + " |")
    return lines


def pack_paragraphs(paragraphs: list[str], budget: int = TOKEN_BUDGET_CHARS) -> list[str]:
    """Greedy-pack paragraphs into budget-sized parts ("\n"-joined).

    A single paragraph longer than ``budget`` is hard-split at character
    boundaries — validate_plan flags any chunk over the token budget, so a
    pathological one-line slide must never pass through whole. Returns at
    least one part when ``paragraphs`` is non-empty.
    """
    parts: list[str] = []
    current = ""
    for paragraph in paragraphs:
        for piece in _budget_pieces(paragraph, budget):
            if not current:
                current = piece
            elif len(current) + len(piece) + 1 <= budget:
                current = f"{current}\n{piece}"
            else:
                parts.append(current)
                current = piece
    if current:
        parts.append(current)
    return parts


def _budget_pieces(paragraph: str, budget: int) -> list[str]:
    """One piece for a fitting paragraph; budget-sized slices otherwise."""
    if len(paragraph) <= budget:
        return [paragraph]
    return [paragraph[start : start + budget] for start in range(0, len(paragraph), budget)]


def _cell_text(cell: object) -> str:
    text = getattr(cell, "text", "")
    return " ".join(text.split()) if isinstance(text, str) else ""


def _company(deck: Presentation) -> str | None:
    """The extended-properties Company (app.xml), or None when absent."""
    try:
        for part in deck.part.package.iter_parts():
            if str(part.partname) != _APP_XML_PART:
                continue
            root = ET.fromstring(part.blob)
            node = root.find(f"{_EXTENDED_NS}Company")
            return _clean_text(node.text) if node is not None else None
    except Exception:
        return None
    return None


def _clean_text(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
