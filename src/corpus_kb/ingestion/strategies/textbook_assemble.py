"""Pure assembly for the textbook strategy: parsed windows to chunks.

No I/O and no parser imports — synthetic window records drive every test.
The pass order is fixed:

1. :func:`flatten_windows` concatenates window records in order and drops
   repeated pages (first window wins); page numbers are the dedupe key.
2. :func:`assign_sections` walks items in page order. Docling labels are
   primary: a ``section_header``/``title`` item opens the section. pypdf
   outline bookmarks fill the gaps where labels are thin: a page that a
   bookmark anchors and that carries no header item adopts the bookmark
   title. A window that starts mid-section carries the open title — the
   walk never resets at a window edge.
3. :func:`section_blocks` merges consecutive same-section items.
4. :func:`plan_chunks` packs whole blocks up to the token budget with the
   shared ``chunking.hierarchy`` primitives and emits chunks carrying
   ``page_start``, ``page_end``, and the section title. Chunk texts are
   exact ``document_text`` slices by construction.

Honest limits: a section spanning many windows keeps its opening title,
and headers Docling missed inside later windows stay unfilled — bookmarks
are the only global structure a windowed parse can see.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...chunking.hierarchy import CHARS_PER_TOKEN, Segment, estimate_tokens, split_oversized
from ..base import PlannedChunk
from .textbook_pages import OutlineBookmark, PageItem, WindowRecord

#: Docling labels that open a section (primary structure signal).
HEADER_LABELS = frozenset({"section_header", "title"})

_JOIN = "\n\n"


@dataclass(frozen=True)
class SectionBlock:
    """One merged run of same-section items across any number of windows."""

    title: str | None
    start_page: int
    end_page: int
    text: str


def flatten_windows(records: list[WindowRecord]) -> list[PageItem]:
    """Window records in order, page-ordered, deduped by page (first wins).

    The dedupe key is the page, not the item: every item of a page from
    the first window that parsed it stays, and a later window's whole
    copy of that page drops. Overlap across a seam keeps one copy.
    """
    items: list[PageItem] = []
    seen: set[int] = set()
    for record in records:
        parsed: set[int] = set()
        for item in sorted(record.items, key=lambda one: one.page):
            if item.page in seen:
                continue
            parsed.add(item.page)
            items.append(item)
        seen |= parsed
    return items


def assign_sections(
    items: list[PageItem], bookmarks: list[OutlineBookmark]
) -> list[tuple[PageItem, str | None]]:
    """Pair every item with its section title (labels primary, bookmarks fill).

    A bookmark fills a page only when that page has no header item — the
    one place Docling labels are thin. Every other page inherits the open
    section, which is how a seam carries across windows.
    """
    anchored = {bookmark.page: bookmark.title for bookmark in bookmarks if bookmark.title}
    header_pages = {
        item.page for item in items if item.label in HEADER_LABELS and item.text.strip()
    }
    current: str | None = None
    paired: list[tuple[PageItem, str | None]] = []
    for item in items:
        title = item.text.strip()
        if item.label in HEADER_LABELS and title:
            current = title
        elif item.page in anchored and item.page not in header_pages:
            current = anchored[item.page]
        paired.append((item, current))
    return paired


def section_blocks(pairs: list[tuple[PageItem, str | None]]) -> list[SectionBlock]:
    """Merge consecutive same-section items into blocks (empty text dropped)."""
    blocks: list[SectionBlock] = []
    for item, title in pairs:
        if not item.text.strip():
            continue
        previous = blocks[-1] if blocks else None
        if previous is not None and previous.title == title:
            blocks[-1] = SectionBlock(
                title=title,
                start_page=previous.start_page,
                end_page=max(previous.end_page, item.page),
                text=_JOIN.join((previous.text, item.text)),
            )
        else:
            blocks.append(SectionBlock(title, item.page, item.page, item.text))
    return blocks


def _line_span(text: str, start: int, end: int) -> tuple[int, int]:
    """1-based line numbers for a char span (the unstructured_chunker rule)."""
    return text.count("\n", 0, start) + 1, text.count("\n", 0, end) + 1


def plan_chunks(
    blocks: list[SectionBlock],
    strategy: str,
    budget_chars: int,
    warnings: list[str],
) -> tuple[str, list[PlannedChunk]]:
    """Pack section blocks into budget-bounded chunks with page metadata.

    Returns ``(document_text, chunks)``. ``document_text`` is the
    ``\\n\\n``-join of the block texts; whole blocks pack greedily up to
    the budget, and one oversized block hard-splits at word boundaries
    through the shared ``split_oversized`` (zero overlap, exact offsets).
    A chunk's section is its opening block's title, so packed blocks keep
    the first section name; ``page_start``/``page_end`` span its blocks.
    """
    target_tokens = max(1, budget_chars // CHARS_PER_TOKEN)
    segments: list[Segment] = []
    cursor = 0
    for block in blocks:
        segments.append(
            Segment(
                text=block.text,
                start_char=cursor,
                end_char=cursor + len(block.text),
                kind="section",
            )
        )
        cursor += len(block.text) + len(_JOIN)
    document_text = _JOIN.join(block.text for block in blocks)

    chunks: list[PlannedChunk] = []
    current: list[tuple[Segment, SectionBlock]] = []
    used = 0

    def emit() -> None:
        nonlocal current, used
        if not current:
            return
        first_seg, first_block = current[0]
        last_block = current[-1][1]
        start, end = first_seg.start_char, current[-1][0].end_char
        start_line, end_line = _line_span(document_text, start, end)
        chunks.append(
            PlannedChunk(
                text=document_text[start:end],
                index=len(chunks),
                metadata={
                    "strategy": strategy,
                    "section": first_block.title,
                    "page_start": first_block.start_page,
                    "page_end": last_block.end_page,
                },
                source_type="text",
                start_line=start_line,
                end_line=end_line,
                source_start_char=start,
                source_end_char=end,
            )
        )
        current = []
        used = 0

    for segment, block in zip(segments, blocks, strict=True):
        tokens = estimate_tokens(segment.text)
        if tokens > target_tokens:
            emit()
            warnings.append(
                f"pages {block.start_page} to {block.end_page} exceed the token budget; split"
            )
            for piece in _bounded_pieces(segment, target_tokens, budget_chars):
                start_line, end_line = _line_span(document_text, piece.start_char, piece.end_char)
                chunks.append(
                    PlannedChunk(
                        text=document_text[piece.start_char : piece.end_char],
                        index=len(chunks),
                        metadata={
                            "strategy": strategy,
                            "section": block.title,
                            "page_start": block.start_page,
                            "page_end": block.end_page,
                        },
                        source_type="text",
                        start_line=start_line,
                        end_line=end_line,
                        source_start_char=piece.start_char,
                        source_end_char=piece.end_char,
                    )
                )
            continue
        if current and used + tokens > target_tokens:
            emit()
        current.append((segment, block))
        used += tokens
    emit()
    return document_text, chunks


def _bounded_pieces(segment: Segment, target_tokens: int, budget_chars: int) -> list[Segment]:
    """Word-boundary pieces within the budget, exact char offsets kept.

    ``split_oversized`` emits one unbreakable run (a word with no
    whitespace) whole and over budget; such runs fall back to exact char
    slices — the code strategy's last resort — so the plan always
    conforms.
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
