"""Textbook assembly: seam carry, page dedupe, outline fill, packing, cache.

Pure-python half of the textbook strategy — no pypdf, no docling, no I/O
beyond ``tmp_path`` for the cache tests. Synthetic window records drive
every scenario, so these tests run in CI where neither optional extra is
installed.
"""

from __future__ import annotations

from pathlib import Path

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
from corpus_kb.ingestion.strategies.textbook_pages import OutlineBookmark, PageItem, WindowRecord
from corpus_kb.ingestion.strategies.textbook_settings import settings_from

HEADER = "section_header"


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
    big = "word " * 3000  # ~15000 chars: forces a split inside one block
    blocks = [
        SectionBlock("Chapter One", 1, 2, "small body"),
        SectionBlock("Chapter Two", 3, 9, big),
    ]
    warnings: list[str] = []
    document_text, chunks = plan_chunks(
        blocks, TEXTBOOK_STRATEGY_NAME, TOKEN_BUDGET_CHARS, warnings
    )
    plan = ChunkPlan(strategy=TEXTBOOK_STRATEGY_NAME, chunks=chunks)
    assert validate_plan(plan) == []
    assert all(len(chunk.text) <= TOKEN_BUDGET_CHARS for chunk in chunks)
    assert any("exceed the token budget" in warning for warning in warnings)
    for chunk in chunks:
        assert document_text[chunk.source_start_char : chunk.source_end_char] == chunk.text
    packed = [chunk for chunk in chunks if chunk.metadata["section"] == "Chapter One"]
    assert packed and packed[0].metadata["page_start"] == 1
    split_chunks = [chunk for chunk in chunks if chunk.metadata["section"] == "Chapter Two"]
    assert split_chunks
    assert all(chunk.metadata["page_start"] == 3 for chunk in split_chunks)
    assert all(chunk.metadata["page_end"] == 9 for chunk in split_chunks)
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
