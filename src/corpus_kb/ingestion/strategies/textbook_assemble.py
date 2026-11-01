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
3. :func:`section_blocks` merges consecutive same-section items into
   blocks that record each item's char span (and page).
4. :func:`plan_chunks` packs whole blocks up to the token budget and
   emits every chunk — packed or hard-split — through ``make_chunk``
   (``textbook_pack``): pages are the true first and last pages the
   chunk's char span touches, and ``heading_path`` carries the section
   title. Chunk texts are exact ``document_text`` slices by construction.
   With ``citations`` set, each chunk's metadata also gains a plain
   ``citation`` string built from its claimed pages and section title.

Honest limits: a section spanning many windows keeps its opening title,
and headers Docling missed inside later windows stay unfilled — bookmarks
are the only global structure a windowed parse can see.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...chunking.hierarchy import CHARS_PER_TOKEN, Segment, estimate_tokens
from ..base import PlannedChunk
from .textbook_pack import PageSpan, bounded_pieces, make_chunk
from .textbook_pages import OutlineBookmark, PageItem, WindowRecord

#: Docling labels that open a section (primary structure signal).
HEADER_LABELS = frozenset({"section_header", "title"})

_JOIN = "\n\n"


@dataclass(frozen=True)
class SectionBlock:
    """One merged run of same-section items across any number of windows.

    ``pages`` records each item's ``(start, end, page)`` span inside
    ``text`` (each item owns the ``_JOIN`` after it); empty when the
    block was built directly and only the block-level range is known.
    """

    title: str | None
    start_page: int
    end_page: int
    text: str
    pages: tuple[tuple[int, int, int], ...] = ()


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
    """Merge consecutive same-section items into blocks (empty text dropped).

    Each block records its items' ``(start, end, page)`` char spans so
    :func:`plan_chunks` can attribute any char range to its true pages.
    """
    blocks: list[SectionBlock] = []
    for item, title in pairs:
        if not item.text.strip():
            continue
        previous = blocks[-1] if blocks else None
        if previous is not None and previous.title == title:
            offset = len(previous.text) + len(_JOIN)
            # The open item's span grows over the join before the new item
            # (spans tile the block text); the new item's span stays exact —
            # no join follows the block end, so the last span ends there.
            last_start, _, last_page = previous.pages[-1]
            blocks[-1] = SectionBlock(
                title=title,
                start_page=previous.start_page,
                end_page=max(previous.end_page, item.page),
                text=_JOIN.join((previous.text, item.text)),
                pages=(
                    *previous.pages[:-1],
                    (last_start, offset, last_page),
                    (offset, offset + len(item.text), item.page),
                ),
            )
        else:
            first = (0, len(item.text), item.page)
            blocks.append(SectionBlock(title, item.page, item.page, item.text, (first,)))
    return blocks


def plan_chunks(
    blocks: list[SectionBlock],
    strategy: str,
    budget_chars: int,
    warnings: list[str],
    *,
    citations: bool = False,
) -> tuple[str, list[PlannedChunk]]:
    """Pack section blocks into budget-bounded chunks with true page metadata.

    Returns ``(document_text, chunks)``. ``document_text`` is the
    ``\\n\\n``-join of the block texts; whole blocks pack greedily up to
    the budget, and one oversized block hard-splits at word boundaries
    through :func:`bounded_pieces` (zero overlap, exact offsets). Every
    chunk goes through :func:`make_chunk`, so its pages are the true
    first and last pages its char span touches, its heading path is its
    opening block's title, and its text is an exact slice. With
    ``citations`` set, each chunk's metadata also carries a ``citation``
    string; flag-off leaves the metadata shape unchanged.
    """
    target_tokens = max(1, budget_chars // CHARS_PER_TOKEN)
    segments: list[Segment] = []
    page_map: list[PageSpan] = []
    cursor = 0
    for block in blocks:
        end = cursor + len(block.text)
        segments.append(Segment(text=block.text, start_char=cursor, end_char=end, kind="section"))
        if block.pages:
            page_map.extend(
                (cursor + start, cursor + stop, page, page) for start, stop, page in block.pages
            )
        else:
            page_map.append((cursor, end, block.start_page, block.end_page))
        cursor = end + len(_JOIN)
    document_text = _JOIN.join(block.text for block in blocks)

    chunks: list[PlannedChunk] = []
    current: list[tuple[Segment, SectionBlock]] = []
    used = 0

    def emit() -> None:
        nonlocal current, used
        if not current:
            return
        chunks.append(
            make_chunk(
                document_text,
                strategy,
                page_map,
                current[0][0].start_char,
                current[-1][0].end_char,
                current[0][1].title,
                len(chunks),
                citation=citations,
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
            for piece in bounded_pieces(segment, target_tokens, budget_chars):
                chunks.append(
                    make_chunk(
                        document_text,
                        strategy,
                        page_map,
                        piece.start_char,
                        piece.end_char,
                        block.title,
                        len(chunks),
                        citation=citations,
                    )
                )
            continue
        if current and used + tokens > target_tokens:
            emit()
        current.append((segment, block))
        used += tokens
    emit()
    return document_text, chunks
