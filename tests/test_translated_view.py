"""Translated view seam mechanics (TD-12 point 5): content-hash keying.

The state-level behaviors live in test_translation_quality.py; this file
pins the view's cache-key contract (content hash over source text and
target language) and the miss-then-request flow.
"""

from __future__ import annotations

import hashlib

from corpus_kb.coding.translated_view import (
    STATE_MISSING,
    _cache_key,
    translated_view,
)


class _RecordingCache:
    def __init__(self) -> None:
        self.asked: list[str] = []

    def get(self, key: str) -> dict[str, str] | None:
        self.asked.append(key)
        return None


def test_cache_key_is_content_hash_over_text_and_language() -> None:
    expected = hashlib.sha256(b"de\nHallo Welt").hexdigest()
    assert _cache_key("Hallo Welt", "de") == expected
    assert _cache_key("Hallo Welt", "en") != expected
    assert _cache_key("Hallo", "de") != expected


def test_view_asks_exactly_the_content_hash() -> None:
    cache = _RecordingCache()
    view = translated_view("u1", "Hallo Welt", "de", cache)
    assert cache.asked == [_cache_key("Hallo Welt", "de")]
    assert view.quality_state == STATE_MISSING


def test_view_is_read_only_shape() -> None:
    view = translated_view("u9", "text", "en", None)
    assert (view.unit_id, view.text, view.source_language, view.target_language) == (
        "u9",
        "",
        "",
        "en",
    )
