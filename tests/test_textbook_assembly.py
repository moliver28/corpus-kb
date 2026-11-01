"""Textbook assembly: seam carry, page dedupe, outline fill, packing, cache.

Pure-python half of the textbook strategy — no pypdf, no docling, no I/O
beyond ``tmp_path`` for the cache tests. Synthetic window records drive
every scenario, so these tests run in CI where neither optional extra is
installed.
"""

from __future__ import annotations

from pathlib import Path

from corpus_kb.chunking.hierarchy import Segment
from corpus_kb.ingestion.base import TOKEN_BUDGET_CHARS, ChunkPlan, validate_plan
from corpus_kb.ingestion.strategies.textbook import TEXTBOOK_STRATEGY_NAME
from corpus_kb.ingestion.strategies.textbook_assemble import (
    SectionBlock,
    assign_sections,
    flatten_windows,
    plan_chunks,
    section_blocks,
)
from corpus_kb.ingestion.strategies.textbook_cache import (
    CacheKey,
    file_sha256,
    load_window_cache,
    save_window_cache,
    window_ranges,
)
from corpus_kb.ingestion.strategies.textbook_pack import bounded_pieces
from corpus_kb.ingestion.strategies.textbook_pages import OutlineBookmark, PageItem, WindowRecord
from corpus_kb.ingestion.strategies.textbook_settings import settings_from

HEADER = "section_header"
JOIN = "\n\n"


def _item(page: int, text: str, label: str = "text") -> PageItem:
    return PageItem(page=page, label=label, text=text)


def _record(start: int, items: list[PageItem]) -> WindowRecord:
    return WindowRecord(start_page=start, end_page=start + 1, items=items)


# ---------------------------------------------------------------------------
# Window ranges and settings
# ---------------------------------------------------------------------------


def test_window_ranges_cover_all_pages_inclusive() -> None:
    assert window_ranges(5, 2) == [(1, 2), (3, 4), (5, 5)]
    assert window_ranges(4, 50) == [(1, 4)]
    assert window_ranges(0, 50) == []


def test_settings_defaults_and_overrides() -> None:
    defaults = settings_from({})
    assert (defaults.min_pages, defaults.window_pages, defaults.parser) == (120, 50, "docling")
    tuned = settings_from(
        {"textbook": {"min_pages": 3, "window_pages": 7, "parser": "pypdf", "cache_dir": "x"}}
    )
    assert (tuned.min_pages, tuned.window_pages, tuned.parser, tuned.cache_dir) == (
        3,
        7,
        "pypdf",
        "x",
    )
    junk = settings_from({"textbook": {"min_pages": "many", "window_pages": -1, "parser": 3}})
    assert (junk.min_pages, junk.window_pages, junk.parser) == (120, 50, "docling")


# ---------------------------------------------------------------------------
# Page dedupe (the seam rule: first window wins, by page)
# ---------------------------------------------------------------------------


def test_flatten_dedupes_repeated_pages_first_window_wins() -> None:
    window_one = _record(1, [_item(1, "one"), _item(2, "two")])
    window_two = _record(3, [_item(2, "two-again"), _item(3, "three")])
    items = flatten_windows([window_one, window_two])
    assert [item.text for item in items] == ["one", "two", "three"]


def test_flatten_orders_pages_within_a_window() -> None:
    scrambled = _record(1, [_item(3, "three"), _item(1, "one"), _item(2, "two")])
    assert [item.page for item in flatten_windows([scrambled])] == [1, 2, 3]


# ---------------------------------------------------------------------------
# Seam carry and outline enrichment
# ---------------------------------------------------------------------------


def _two_window_book() -> list[WindowRecord]:
    """Window 1 ends inside Chapter One; window 2 opens with no header."""
    window_one = _record(
        1,
        [
            _item(1, "Chapter One", HEADER),
            _item(1, "first body"),
            _item(2, "carried body"),
        ],
    )
    window_two = _record(
        3,
        [
            _item(3, "seam body"),
            _item(4, "Chapter Two", HEADER),
            _item(4, "second body"),
        ],
    )
    return [window_one, window_two]


def test_seam_carries_open_section_across_windows() -> None:
    pairs = assign_sections(flatten_windows(_two_window_book()), [])
    sections = {item.text: title for item, title in pairs}
    assert sections["seam body"] == "Chapter One"
    assert sections["second body"] == "Chapter Two"


def test_section_spanning_many_windows_keeps_opening_title() -> None:
    records = [
        _record(
            start,
            [_item(start, f"body {start}")],
        )
        for start in (2, 4, 6)
    ]
    head = _record(1, [_item(1, "Chapter One", HEADER), _item(1, "intro")])
    pairs = assign_sections(flatten_windows([head, *records]), [])
    assert {title for _, title in pairs} == {"Chapter One"}


def test_bookmark_fills_page_where_labels_are_thin() -> None:
    items = [_item(5, "headerless prose")]
    pairs = assign_sections(items, [OutlineBookmark("Chapter Two", 5, 0)])
    assert pairs[0][1] == "Chapter Two"


def test_bookmark_never_overrides_a_docling_header() -> None:
    items = [_item(5, "Chapter Two", HEADER), _item(5, "prose")]
    pairs = assign_sections(items, [OutlineBookmark("Bookmark Title", 5, 0)])
    assert {title for _, title in pairs} == {"Chapter Two"}


def test_bookmark_carry_runs_until_the_next_title() -> None:
    items = [_item(3, "anchored"), _item(4, "after")]
    pairs = assign_sections(items, [OutlineBookmark("Part Two", 3, 0)])
    assert [title for _, title in pairs] == ["Part Two", "Part Two"]


def test_blocks_merge_same_section_and_track_pages() -> None:
    pairs = assign_sections(flatten_windows(_two_window_book()), [])
    blocks = section_blocks(pairs)
    assert [(block.title, block.start_page, block.end_page) for block in blocks] == [
        ("Chapter One", 1, 3),
        ("Chapter Two", 4, 4),
    ]


# ---------------------------------------------------------------------------
# Packing: budget, page metadata, offset invariant
# ---------------------------------------------------------------------------


def test_plan_chunks_budget_pages_and_offsets() -> None:
    items = [
        _item(1, "Chapter One", HEADER),
        _item(2, "small body"),
        _item(3, "Chapter Two", HEADER),
        *(_item(page, "word " * 700) for page in range(3, 10)),
    ]
    warnings: list[str] = []
    document_text, chunks = plan_chunks(
        section_blocks(assign_sections(items, [])),
        TEXTBOOK_STRATEGY_NAME,
        TOKEN_BUDGET_CHARS,
        warnings,
    )
    plan = ChunkPlan(strategy=TEXTBOOK_STRATEGY_NAME, chunks=chunks)
    assert validate_plan(plan) == []
    assert all(len(chunk.text) <= TOKEN_BUDGET_CHARS for chunk in chunks)
    assert any("exceed the token budget" in warning for warning in warnings)
    for chunk in chunks:
        assert document_text[chunk.source_start_char : chunk.source_end_char] == chunk.text
    packed = chunks[0]
    assert packed.metadata["section"] == "Chapter One"
    assert (packed.metadata["page_start"], packed.metadata["page_end"]) == (1, 2)
    splits = chunks[1:]
    assert splits and all(split.metadata["section"] == "Chapter Two" for split in splits)
    assert all(
        3 <= split.metadata["page_start"] <= split.metadata["page_end"] <= 9 for split in splits
    )
    assert splits[0].metadata["page_start"] == 3
    assert splits[-1].metadata["page_end"] == 9
    assert any(split.metadata["page_end"] < 9 for split in splits)  # pieces stop at their text
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))


def test_plan_chunks_packs_small_blocks_together() -> None:
    blocks = [
        SectionBlock("One", 1, 1, "a body"),
        SectionBlock("Two", 2, 2, "b body"),
    ]
    _, chunks = plan_chunks(blocks, TEXTBOOK_STRATEGY_NAME, TOKEN_BUDGET_CHARS, [])
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.text == "a body\n\nb body"
    assert chunk.metadata == {
        "strategy": TEXTBOOK_STRATEGY_NAME,
        "section": "One",
        "page_start": 1,
        "page_end": 2,
    }


def test_split_pieces_claim_disjoint_true_page_sets() -> None:
    page_text = "word " * 819  # 819 two-token words: one split piece per page item
    items = [_item(page, page_text) for page in (3, 4, 5)]
    blocks = section_blocks(assign_sections(items, []))
    _, chunks = plan_chunks(blocks, TEXTBOOK_STRATEGY_NAME, TOKEN_BUDGET_CHARS, [])
    assert len(chunks) == 3
    ranges = [(chunk.metadata["page_start"], chunk.metadata["page_end"]) for chunk in chunks]
    assert ranges == [(3, 3), (4, 4), (5, 5)]  # each piece stops at its own page
    claimed = {page for start, end in ranges for page in range(start, end + 1)}
    assert claimed == {3, 4, 5}  # the pieces' union is the block's full range


def test_split_pieces_carry_the_section_heading_path() -> None:
    page_text = "word " * 700
    items = [_item(2, "Chapter Deep", HEADER), *(_item(page, page_text) for page in (3, 4, 5))]
    blocks = section_blocks(assign_sections(items, []))
    _, chunks = plan_chunks(blocks, TEXTBOOK_STRATEGY_NAME, TOKEN_BUDGET_CHARS, [])
    assert len(chunks) > 1
    assert all(chunk.heading_path == ["Chapter Deep"] for chunk in chunks)
    assert all(chunk.metadata["section"] == "Chapter Deep" for chunk in chunks)
    assert chunks[0].metadata["page_start"] == 2
    assert chunks[-1].metadata["page_end"] == 5
    assert any(chunk.metadata["page_end"] < 5 for chunk in chunks)  # no piece claims it all


def test_packed_chunk_page_range_is_exact_across_a_window_seam() -> None:
    blocks = section_blocks(assign_sections(flatten_windows(_two_window_book()), []))
    _, chunks = plan_chunks(blocks, TEXTBOOK_STRATEGY_NAME, 60, [])  # 60 chars: one block each
    assert [(chunk.metadata["page_start"], chunk.metadata["page_end"]) for chunk in chunks] == [
        (1, 3),
        (4, 4),
    ]


def test_plan_chunks_sets_heading_path_from_the_opening_block() -> None:
    blocks = [
        SectionBlock("One", 1, 1, "a body"),
        SectionBlock(None, 2, 2, "b body"),
    ]
    _, chunks = plan_chunks(blocks, TEXTBOOK_STRATEGY_NAME, 8, [])  # 8 chars: one block each
    assert [chunk.heading_path for chunk in chunks] == [["One"], None]
    assert [chunk.metadata["section"] for chunk in chunks] == ["One", None]
    assert "heading_path" not in chunks[0].metadata


def test_blocks_without_item_spans_fall_back_to_block_level_pages() -> None:
    # A directly built block only knows its page range: every split piece
    # keeps that range — the honest limit when no item-level map exists.
    blocks = [SectionBlock("Wide", 3, 9, "word " * 3000)]
    _, chunks = plan_chunks(blocks, TEXTBOOK_STRATEGY_NAME, TOKEN_BUDGET_CHARS, [])
    assert len(chunks) > 1
    assert all(
        (chunk.metadata["page_start"], chunk.metadata["page_end"]) == (3, 9) for chunk in chunks
    )


def _audit_book() -> list[WindowRecord]:
    """Window 1 ends inside Part One; page 4 is headerless (bookmark); 5-7 split."""
    page_text = "word " * 819
    return [
        _record(
            1,
            [
                _item(1, "Part One", HEADER),
                _item(1, "intro on one"),
                _item(2, "body on two"),
                _item(2, "   "),  # blank text: dropped
            ],
        ),
        _record(3, [_item(3, "carried to three"), _item(4, "prose on four")]),
        _record(
            5,
            [
                _item(5, "Chapter Deep", HEADER),
                _item(5, page_text),
                _item(6, page_text),
                _item(7, page_text),
            ],
        ),
    ]


def test_audit_rebuilds_true_page_ranges_and_exact_slices() -> None:
    pairs = assign_sections(flatten_windows(_audit_book()), [OutlineBookmark("Part Two", 4, 0)])
    document_text, chunks = plan_chunks(
        section_blocks(pairs), TEXTBOOK_STRATEGY_NAME, TOKEN_BUDGET_CHARS, []
    )
    plan = ChunkPlan(strategy=TEXTBOOK_STRATEGY_NAME, chunks=chunks)
    assert validate_plan(plan) == []
    assert all(len(chunk.text) <= TOKEN_BUDGET_CHARS for chunk in chunks)

    # Independent reconstruction (the program bench's mirror): regroup the
    # paired items by hand, rebuild the document text, and derive each
    # item's char span (each item owns the join after it).
    spans: list[tuple[int, int, int]] = []
    rebuilt: list[str] = []
    group: list[tuple[PageItem, str | None]] = []
    cursor = 0

    def flush() -> None:
        nonlocal cursor
        if not group:
            return
        text = JOIN.join(item.text for item, _ in group)
        rebuilt.append(text)
        offset = cursor
        for position, (item, _) in enumerate(group):
            stop = (
                cursor + len(text)
                if position == len(group) - 1
                else offset + len(item.text) + len(JOIN)
            )
            spans.append((offset, stop, item.page))
            offset = stop
        cursor += len(text) + len(JOIN)
        group.clear()

    for item, title in pairs:
        if not item.text.strip():
            continue
        if group and group[-1][1] != title:
            flush()
        group.append((item, title))
    flush()

    assert JOIN.join(rebuilt) == document_text
    for chunk in chunks:
        start, end = chunk.source_start_char, chunk.source_end_char
        true_pages = {page for lo, hi, page in spans if lo < end and hi > start}
        assert true_pages
        assert (chunk.metadata["page_start"], chunk.metadata["page_end"]) == (
            min(true_pages),
            max(true_pages),
        )
        assert document_text[start:end] == chunk.text
        expected = [chunk.metadata["section"]] if chunk.metadata["section"] else None
        assert chunk.heading_path == expected


# ---------------------------------------------------------------------------
# Cache round trip and invalidation
# ---------------------------------------------------------------------------


def test_cache_round_trip_preserves_the_record(tmp_path: Path) -> None:
    record = _record(3, [_item(3, "text", "text"), _item(4, "head", HEADER)])
    key = CacheKey("docling", "a" * 64, 1)
    path = key.path(tmp_path)
    save_window_cache(path, key, record)
    loaded = load_window_cache(path, key, 3, 4)
    assert loaded == record


def test_cache_invalidates_on_any_key_change(tmp_path: Path) -> None:
    record = _record(1, [_item(1, "text")])
    key = CacheKey("docling", "a" * 64, 0)
    path = key.path(tmp_path)
    save_window_cache(path, key, record)
    assert load_window_cache(path, CacheKey("docling", "b" * 64, 0), 1, 2) is None  # file changed
    assert load_window_cache(path, CacheKey("pypdf", "a" * 64, 0), 1, 2) is None  # parser changed
    assert load_window_cache(path, CacheKey("docling", "a" * 64, 1), 1, 2) is None  # window moved


def test_cache_corrupt_or_missing_file_is_a_miss(tmp_path: Path) -> None:
    key = CacheKey("docling", "a" * 64, 0)
    path = key.path(tmp_path)
    assert load_window_cache(path, key, 1, 2) is None
    path.write_text("{not json", encoding="utf-8")
    assert load_window_cache(path, key, 1, 2) is None
    path.write_text('{"parser": "docling-v1"}', encoding="utf-8")
    assert load_window_cache(path, key, 1, 2) is None


def test_file_sha256_tracks_content(tmp_path: Path) -> None:
    path = tmp_path / "book.pdf"
    path.write_bytes(b"one")
    first = file_sha256(path)
    path.write_bytes(b"two")
    assert file_sha256(path) != first


def test_bounded_pieces_returns_fitting_text_whole() -> None:
    # Text within the budget passes through split_oversized untouched.
    segment = Segment(text="one two three", start_char=10, end_char=23, kind="section")
    pieces = bounded_pieces(segment, target_tokens=10, budget_chars=200)
    assert len(pieces) == 1
    assert pieces[0].text == "one two three"
    assert (pieces[0].start_char, pieces[0].end_char) == (10, 23)


def test_bounded_pieces_slices_unbreakable_runs_to_budget() -> None:
    # A run with no whitespace cannot split at words, so split_oversized
    # emits it whole and over budget; the fixed-step fallback slices it
    # while keeping exact source offsets, like the code strategy does.
    run = "x" * 500
    segment = Segment(text=run, start_char=100, end_char=600, kind="section")
    pieces = bounded_pieces(segment, target_tokens=10, budget_chars=200)
    assert [piece.text for piece in pieces] == [run[0:200], run[200:400], run[400:500]]
    assert [piece.start_char for piece in pieces] == [100, 300, 500]
    assert [piece.end_char for piece in pieces] == [300, 500, 600]
    assert all(piece.kind == "split" for piece in pieces)
