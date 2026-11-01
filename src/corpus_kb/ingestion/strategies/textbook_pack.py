"""Chunk drafting for the textbook plan: one builder, true pages.

Every chunk ``plan_chunks`` emits — packed or hard-split — goes through
:func:`make_chunk`, so all fields derive in one place. The page map is a
flat list of :data:`PageSpan` char-span entries over ``document_text``;
entries tile the text, so the first and last entries a chunk's span
touches fix its true page range. :func:`bounded_pieces` wraps the shared
``split_oversized`` for oversized blocks (zero overlap, exact offsets).

Units, honest limits: a chunk whose pages hold parsed tables carries a
sorted ``units`` metadata key, so today that list is always
``["table"]``. Figures and formulas join the list when their detection
lands. Units ride the ``structured_output`` flag (a unit needs the rich
parse), and they live in chunk metadata, the same durable channel as
``citation`` — they never surface in search results.

Pure stdlib; no I/O and no parser imports.
"""

from __future__ import annotations

from collections.abc import Iterable

from ...chunking.hierarchy import Segment, split_oversized
from ..base import PlannedChunk
from .textbook_pages import PageItem

#: One page-map entry: ``[start, end)`` chars of ``document_text`` and the
#: ``first..last`` pages they sit on (a single page for per-item entries).
PageSpan = tuple[int, int, int, int]

#: One units-map entry: a 1-based page and the sorted names of the units
#: parsed on it. Empty (or None) when the rich parse found no units.
UnitMap = dict[int, tuple[str, ...]]


def _line_span(text: str, start: int, end: int) -> tuple[int, int]:
    """1-based line numbers for a char span (the unstructured_chunker rule)."""
    return text.count("\n", 0, start) + 1, text.count("\n", 0, end) + 1


def page_range(page_map: list[PageSpan], start: int, end: int) -> tuple[int, int]:
    """First and last page of the entries a ``[start, end)`` span touches."""
    hits = [entry for entry in page_map if entry[0] < end and entry[1] > start]
    return hits[0][2], hits[-1][3]


def page_units(items: Iterable[PageItem]) -> UnitMap:
    """The page-to-units map: one entry per page that parsed a unit.

    A unit today is a table: an item whose ``table_markdown`` is set,
    and that field fills only behind ``structured_output``. So the flag
    off gives an empty map, and the map never carries a page the parse
    did not mark. Sorted tuples keep every later read deterministic.
    """
    units: dict[int, set[str]] = {}
    for item in items:
        if item.table_markdown is not None:
            units.setdefault(item.page, set()).add("table")
    return {page: tuple(sorted(names)) for page, names in units.items()}


def _citation_text(page_start: int, page_end: int, title: str | None) -> str:
    """A plain-English citation: the true pages, then the section title.

    One page reads ``p. 34``; a range reads ``pp. 34-36``; a known
    section appends after `` - `` (``pp. 34-36 - Section 2.1``).
    """
    pages = f"p. {page_start}" if page_start == page_end else f"pp. {page_start}-{page_end}"
    return f"{pages} - {title}" if title else pages


def make_chunk(
    document_text: str,
    strategy: str,
    page_map: list[PageSpan],
    start: int,
    end: int,
    title: str | None,
    index: int,
    *,
    citation: bool = False,
    units: UnitMap | None = None,
) -> PlannedChunk:
    """Build one chunk draft over ``document_text[start:end]`` — the one builder.

    Both ``plan_chunks`` call sites (greedy packing and hard split)
    derive every field here: the text is an exact slice, the claimed
    pages come from the page map, and the heading path carries the
    opening block's title (or None when it has none). With ``citation``
    set and pages known, the metadata gains a ``citation`` string built
    from those same claimed pages. With a ``units`` map that names a
    unit on one of those pages, the metadata gains the sorted ``units``
    list (each name once); a None or empty map keeps the metadata exact.
    """
    page_start, page_end = page_range(page_map, start, end)
    start_line, end_line = _line_span(document_text, start, end)
    metadata: dict[str, object] = {
        "strategy": strategy,
        "section": title,
        "page_start": page_start,
        "page_end": page_end,
    }
    if citation and page_start > 0:
        # Same pages as the entries above: the citation cites what the
        # page map resolved for this chunk's char span, nothing else.
        metadata["citation"] = _citation_text(page_start, page_end, title)
    if units:
        names = sorted(
            {name for page in range(page_start, page_end + 1) for name in units.get(page, ())}
        )
        if names:
            metadata["units"] = names
    return PlannedChunk(
        text=document_text[start:end],
        index=index,
        metadata=metadata,
        source_type="text",
        heading_path=[title] if title else None,
        start_line=start_line,
        end_line=end_line,
        source_start_char=start,
        source_end_char=end,
    )


def bounded_pieces(segment: Segment, target_tokens: int, budget_chars: int) -> list[Segment]:
    """Word-boundary pieces within the budget, exact char offsets kept.

    ``split_oversized`` emits one unbreakable run (a word with no
    whitespace) whole and over budget; such runs fall back to slices at
    fixed char steps, the same last resort the code strategy uses, so
    the plan always conforms.
    """
    pieces = split_oversized(segment, target_tokens, 0)
    if all(len(piece.text) <= budget_chars for piece in pieces):
        return pieces
    bounded: list[Segment] = []
    for piece in pieces:
        for offset in range(0, len(piece.text), budget_chars):
            bounded.append(
                Segment(
                    text=piece.text[offset : offset + budget_chars],
                    start_char=piece.start_char + offset,
                    end_char=piece.start_char + min(offset + budget_chars, len(piece.text)),
                    kind="split",
                )
            )
    return bounded
