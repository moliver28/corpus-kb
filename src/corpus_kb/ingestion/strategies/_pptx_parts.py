"""PPTX part renderers: shape paragraphs, GitHub tables, budget packing.

Pure helpers over python-pptx objects so :mod:`corpus_kb.ingestion.strategies.pptx`
stays an orchestration module. Every helper is total: an unreadable shape
warns and is skipped; unsupported categories degrade loudly, never vanish —
charts render best-effort category/value tables, groups recurse into
sub-shape text, pictures warn (no OCR in scope).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from defusedxml.ElementTree import fromstring as parse_untrusted_xml
from pptx.chart.chart import Chart
from pptx.presentation import Presentation
from pptx.shapes.autoshape import Shape
from pptx.shapes.base import BaseShape
from pptx.shapes.graphfrm import GraphicFrame
from pptx.shapes.group import GroupShape
from pptx.shapes.picture import Picture
from pptx.slide import Slide
from pptx.table import Table

from ..base import TOKEN_BUDGET_CHARS


class _ChartPlot(Protocol):
    """The plot surface chart markdown rendering reads.

    python-pptx has per-type plot classes (BarPlot, LinePlot, ...) with no
    public common base, so the read surface is declared structurally.
    """

    @property
    def categories(self) -> Sequence[object]: ...

    @property
    def series(self) -> Sequence[object]: ...


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


def slide_hidden(slide: Slide) -> bool:
    """Whether the slide is hidden (the sld element's ``show="0"``)."""
    return slide.element.get("show") == "0"


def slide_paragraphs(slide: Slide, slide_number: int, warnings: list[str]) -> list[str]:
    """Shape text + table/chart markdown lines in reading order (shape order).

    The title placeholder is included like any other text shape — on a title
    slide it is the slide's whole body. Unsupported shapes degrade loudly:
    charts render best-effort category/value tables, groups recurse into
    sub-shapes, pictures warn-only — never a silent drop.
    """
    paragraphs: list[str] = []
    for shape in slide.shapes:
        paragraphs.extend(_shape_paragraphs(shape, slide_number, warnings))
    return paragraphs


def _shape_paragraphs(shape: BaseShape, slide_number: int, warnings: list[str]) -> list[str]:
    """One shape's text/markdown lines; a warning for anything degraded."""
    try:
        if isinstance(shape, GraphicFrame):
            if shape.has_table:
                return table_markdown_lines(shape.table)
            if shape.has_chart:
                return chart_markdown_lines(shape.chart, slide_number, warnings)
        elif isinstance(shape, GroupShape):
            return _group_paragraphs(shape, slide_number, warnings)
        elif isinstance(shape, Picture):
            warnings.append(
                f"slide {slide_number}: unsupported shape 'Picture' skipped (no OCR in scope)"
            )
            return []
        elif isinstance(shape, Shape):
            return [
                paragraph.text.strip()
                for paragraph in shape.text_frame.paragraphs
                if paragraph.text.strip()
            ]
    except Exception as exc:
        warnings.append(f"slide {slide_number}: unreadable shape skipped: {exc}")
        return []
    warnings.append(f"slide {slide_number}: unsupported shape {_shape_kind(shape)} skipped")
    return []


def _group_paragraphs(group: GroupShape, slide_number: int, warnings: list[str]) -> list[str]:
    """Sub-shape text lines from a group shape (recursed, with a warning)."""
    warnings.append(
        f"slide {slide_number}: unsupported shape 'Group' ('{_shape_name(group)}'); "
        "sub-shape text inlined"
    )
    paragraphs: list[str] = []
    for sub_shape in group.shapes:
        paragraphs.extend(_shape_paragraphs(sub_shape, slide_number, warnings))
    return paragraphs


def chart_markdown_lines(chart: Chart, slide_number: int, warnings: list[str]) -> list[str]:
    """Best-effort category/value markdown table for a chart's plots.

    The chart object itself is not chunked — only categories and series
    values are surfaced. Failure warns and yields no lines; success still
    warns that the shape was reduced.
    """
    try:
        lines: list[str] = []
        for plot in chart.plots:
            lines.extend(_plot_markdown_lines(plot))
    except Exception as exc:
        warnings.append(
            f"slide {slide_number}: unsupported shape 'Chart' (graphic frame) skipped: {exc}"
        )
        return []
    warnings.append(
        f"slide {slide_number}: unsupported shape 'Chart' (graphic frame); "
        "categories/values rendered as markdown table"
    )
    return lines


def _plot_markdown_lines(plot: _ChartPlot) -> list[str]:
    """One plot: categories as the first markdown column, one per series."""
    categories = [_escape_cell(str(category)) for category in plot.categories]
    names = [_series_name(series, index) for index, series in enumerate(plot.series)]
    values = [list(getattr(series, "values", ()) or ()) for series in plot.series]
    height = max(len(categories), *(len(column) for column in values))
    if height == 0:
        return []
    lines = [
        "| Category | " + " | ".join(names) + " |",
        "| " + " | ".join(["---"] * (1 + len(names))) + " |",
    ]
    for row in range(height):
        cells = [categories[row] if row < len(categories) else ""]
        cells.extend(str(column[row]) if row < len(column) else "" for column in values)
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def _series_name(series: object, index: int) -> str:
    """The series' name, or a positional fallback when unnamed."""
    name = str(getattr(series, "name", "") or "").strip()
    return _escape_cell(name) or f"series {index + 1}"


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

    Cell text is whitespace-collapsed and pipe-escaped so every row stays
    one intact line of the table.
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


def _escape_cell(text: str) -> str:
    """Escape pipes so one cell can never break the enclosing markdown table."""
    return text.replace("|", "\\|")


def _cell_text(cell: object) -> str:
    text = getattr(cell, "text", "")
    return _escape_cell(" ".join(text.split())) if isinstance(text, str) else ""


def _shape_kind(shape: BaseShape) -> str:
    """A stable name for the shape (shape-type name, else the class name)."""
    try:
        name = getattr(shape.shape_type, "name", None)
    except Exception:
        name = None
    return str(name) if name else type(shape).__name__


def _shape_name(shape: BaseShape) -> str:
    """The shape's name attribute (pipe-escaped), or its kind when unnamed."""
    name = _escape_cell(str(getattr(shape, "name", "") or "").strip())
    return name or _shape_kind(shape)


def _company(deck: Presentation) -> str | None:
    """The extended-properties Company (app.xml), or None when absent."""
    try:
        for part in deck.part.package.iter_parts():
            if str(part.partname) != _APP_XML_PART:
                continue
            root = parse_untrusted_xml(part.blob)
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
