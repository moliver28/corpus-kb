"""The window cache: rich-field round trip and legacy-record compatibility.

The save side serializes rich keys only when the item set them: a pypdf
row holds three keys, and a docling row also carries level/bbox once
the walk provides them (absorbed by the docling-v2 cache tag). The load
side parses rich keys when present and misses on a malformed value (the
caller re-parses, never guesses). Keys come from :class:`CacheKey`,
whose ``parser_version`` resolves plain parser names and passes
rich-variant tags through.
"""

from __future__ import annotations

import json
from pathlib import Path

from corpus_kb.ingestion.strategies.textbook_cache import (
    CacheKey,
    load_window_cache,
    save_window_cache,
)
from corpus_kb.ingestion.strategies.textbook_docling import PageItem
from corpus_kb.ingestion.strategies.textbook_pages import WindowRecord

_TAG = "docling-v2-rich"
_SHA = "a" * 64


def _rich_record() -> WindowRecord:
    items = [
        PageItem(2, "section_header", "Chapter Two", level=1, bbox=(0.0, 1.0, 2.0, 3.0)),
        PageItem(2, "table", "| a |", table_markdown="| a |"),
        PageItem(3, "text", "plain body"),
    ]
    return WindowRecord(start_page=1, end_page=4, items=items)


def test_round_trip_keeps_rich_fields(tmp_path: Path) -> None:
    key = CacheKey(_TAG, _SHA, 1)
    assert key.parser_version == _TAG  # a rich tag passes through as-is
    path = key.path(tmp_path)
    record = _rich_record()
    save_window_cache(path, key, record)
    loaded = load_window_cache(path, key, 1, 4)
    assert loaded == record
    assert loaded is not None and loaded.items[0].bbox == (0.0, 1.0, 2.0, 3.0)
    assert loaded.items[1].table_markdown == "| a |"


def test_plain_parser_key_resolves_the_version_tag() -> None:
    assert CacheKey("docling", _SHA, 0).parser_version == "docling-v2"
    assert CacheKey("pypdf", _SHA, 0).parser_version == "pypdf-v1"


def test_plain_items_serialize_the_legacy_payload_bytes(tmp_path: Path) -> None:
    key = CacheKey(_TAG, _SHA, 1)
    path = key.path(tmp_path)
    record = WindowRecord(
        start_page=1,
        end_page=2,
        items=[PageItem(1, "text", "one"), PageItem(2, "text", "two")],
    )
    save_window_cache(path, key, record)
    legacy_payload = {
        "parser": _TAG,
        "sha256": _SHA,
        "window": 1,
        "start_page": 1,
        "end_page": 2,
        "pages": [
            {"page": 1, "label": "text", "text": "one"},
            {"page": 2, "label": "text", "text": "two"},
        ],
    }
    expected = json.dumps(legacy_payload, ensure_ascii=False, sort_keys=True)
    assert path.read_text(encoding="utf-8") == expected


def test_legacy_record_without_rich_keys_still_loads(tmp_path: Path) -> None:
    key = CacheKey(_TAG, _SHA, 1)
    path = key.path(tmp_path)
    legacy = {
        "parser": _TAG,
        "sha256": _SHA,
        "window": 1,
        "start_page": 1,
        "end_page": 1,
        "pages": [{"page": 1, "label": "text", "text": "old body"}],
    }
    path.write_text(json.dumps(legacy), encoding="utf-8")
    loaded = load_window_cache(path, key, 1, 1)
    assert loaded == WindowRecord(1, 1, [PageItem(1, "text", "old body")])
    assert loaded is not None and loaded.items[0].level is None


def test_malformed_rich_value_misses_the_cache(tmp_path: Path) -> None:
    key = CacheKey(_TAG, _SHA, 1)
    path = key.path(tmp_path)
    broken = {
        "parser": _TAG,
        "sha256": _SHA,
        "window": 1,
        "start_page": 1,
        "end_page": 1,
        "pages": [{"page": 1, "label": "text", "text": "body", "level": "deep"}],
    }
    path.write_text(json.dumps(broken), encoding="utf-8")
    assert load_window_cache(path, key, 1, 1) is None
