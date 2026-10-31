"""Tests for the subprocess window wrapper, cache cap, and env warnings.

The process boundary is patched at the ``_start_child`` seam inside
``textbook_subprocess``, so no test starts a real child process. The
remaining sections cover the window-cache cap (issue #221), the
onnxruntime/docling conflict warning (issue #220), and the settings
parsing for the three new ``textbook:`` keys — all pure and fast.
"""

from __future__ import annotations

import os
from pathlib import Path
from queue import Empty

import pytest

import corpus_kb.ingestion.strategies.textbook_pages as textbook_pages
from corpus_kb.ingestion.strategies.textbook_cache import prune_window_cache
from corpus_kb.ingestion.strategies.textbook_pages import PageItem
from corpus_kb.ingestion.strategies.textbook_settings import settings_from
from corpus_kb.ingestion.strategies.textbook_subprocess import (
    _items_from_rows,
    parse_window_in_subprocess,
)

_MIB = 1024 * 1024


# ---------------------------------------------------------------------------
# Fakes for the spawn seam (no real child process ever starts)
# ---------------------------------------------------------------------------


class _FakeProc:
    """BaseProcess double: join/is_alive/exitcode without a real child.

    ``join`` adds its timeout to ``joined_seconds`` (the arg is real for
    the fakes too). ``hang_past_reap`` models a child stuck in native
    code: joins and terminate never reap it; only the parent's kill does.
    """

    def __init__(self, exitcode: int | None, hang_past_reap: bool = False) -> None:
        self.exitcode = exitcode
        self.terminated = False
        self.killed = False
        self.joined_seconds = 0.0
        self._hang_past_reap = hang_past_reap

    def join(self, timeout: float | None = None) -> None:
        self.joined_seconds += 0.0 if timeout is None else timeout

    def is_alive(self) -> bool:
        if self.exitcode is not None or self.killed:
            return False
        return not (self.terminated and not self._hang_past_reap)

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


class _FakeQueue:
    """Payload-queue double: canned rows, or Empty on every read."""

    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self._rows = rows
        self.closed = False

    def get(self, timeout: float | None = None) -> list[dict[str, object]]:
        del timeout
        if self._rows is None:
            raise Empty
        return self._rows

    def put(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def close(self) -> None:
        self.closed = True


def _patch_child(monkeypatch: pytest.MonkeyPatch, proc: _FakeProc, queue: _FakeQueue) -> None:
    monkeypatch.setattr(
        "corpus_kb.ingestion.strategies.textbook_subprocess._start_child",
        lambda pdf_path, start, end, structured=False: (proc, queue),
    )


_ROWS: list[dict[str, object]] = [
    {"page": 2, "label": "text", "text": "window body"},
    {"page": 3, "label": "section_header", "text": "Chapter Two"},
]


# ---------------------------------------------------------------------------
# parse_window_in_subprocess: crash, timeout, success, no payload
# ---------------------------------------------------------------------------


def test_crashed_child_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = _FakeProc(exitcode=1)
    _patch_child(monkeypatch, proc, _FakeQueue())  # rows None: get raises Empty
    assert parse_window_in_subprocess(Path("book.pdf"), 1, 4, 5.0) is None


def test_child_exit_zero_without_payload_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = _FakeProc(exitcode=0)
    _patch_child(monkeypatch, proc, _FakeQueue())
    assert parse_window_in_subprocess(Path("book.pdf"), 1, 4, 5.0) is None


def test_successful_payload_becomes_page_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = _FakeProc(exitcode=0)
    _patch_child(monkeypatch, proc, _FakeQueue(_ROWS))
    items = parse_window_in_subprocess(Path("book.pdf"), 1, 4, 5.0)
    assert items == [
        PageItem(2, "text", "window body"),
        PageItem(3, "section_header", "Chapter Two"),
    ]


def test_malformed_rows_are_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = _FakeProc(exitcode=0)
    rows: list[dict[str, object]] = [
        {"page": True, "label": "text", "text": "bool page"},
        {"page": 4, "text": "no label"},
        {"page": 5, "label": "text", "text": "kept"},
    ]
    _patch_child(monkeypatch, proc, _FakeQueue(rows))
    items = parse_window_in_subprocess(Path("book.pdf"), 1, 4, 5.0)
    assert items == [PageItem(5, "text", "kept")]


def test_hung_child_is_terminated_and_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = _FakeProc(exitcode=None)  # alive forever: the timeout path
    queue = _FakeQueue()
    _patch_child(monkeypatch, proc, queue)
    items = parse_window_in_subprocess(Path("book.pdf"), 1, 4, 0.05)
    assert items is None
    assert proc.terminated
    assert not proc.is_alive()  # terminate reaped this well-behaved fake
    assert queue.closed  # the queue handle is released on the early return


def test_a_child_alive_past_the_reap_grace_is_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = _FakeProc(exitcode=None, hang_past_reap=True)  # stays alive after shipping
    queue = _FakeQueue(_ROWS)
    _patch_child(monkeypatch, proc, queue)
    items = parse_window_in_subprocess(Path("book.pdf"), 1, 4, 5.0)
    assert [item.page for item in items] == [2, 3]  # the shipped payload still wins
    assert proc.terminated  # terminate came first...
    assert proc.killed  # ...and the kill escalation reaped the hung child
    assert not proc.is_alive()
    assert queue.closed  # the queue handle is released after the reap


def test_items_from_rows_filters_bad_shapes() -> None:
    items = _items_from_rows(
        [
            {"page": 1, "label": "text", "text": "one"},
            {"page": 2, "label": 3, "text": "bad label"},
            {"page": "3", "label": "text", "text": "bad page"},
        ]
    )
    assert [item.page for item in items] == [1]


def test_rows_carry_rich_fields_into_the_items(monkeypatch: pytest.MonkeyPatch) -> None:
    rows: list[dict[str, object]] = [
        {
            "page": 2,
            "label": "section_header",
            "text": "Chapter Two",
            "level": 1,
            "bbox": [0.0, 1.0, 2.0, 3.0],
        },
        {"page": 3, "label": "table", "text": "| a |", "table_markdown": "| a |"},
    ]
    proc = _FakeProc(exitcode=0)
    _patch_child(monkeypatch, proc, _FakeQueue(rows))
    items = parse_window_in_subprocess(Path("book.pdf"), 1, 4, 5.0, structured=True)
    assert items == [
        PageItem(2, "section_header", "Chapter Two", level=1, bbox=(0.0, 1.0, 2.0, 3.0)),
        PageItem(3, "table", "| a |", table_markdown="| a |"),
    ]


def test_a_malformed_rich_row_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    rows: list[dict[str, object]] = [
        {"page": 4, "label": "text", "text": "bad bbox", "bbox": [1.0, 2.0]},
        {"page": 5, "label": "text", "text": "kept"},
    ]
    proc = _FakeProc(exitcode=0)
    _patch_child(monkeypatch, proc, _FakeQueue(rows))
    items = parse_window_in_subprocess(Path("book.pdf"), 1, 4, 5.0)
    assert items == [PageItem(5, "text", "kept")]


# ---------------------------------------------------------------------------
# prune_window_cache: oldest-first eviction, cap off, unreadable files
# ---------------------------------------------------------------------------


def _cache_file(cache_dir: Path, name: str, size: int, mtime: float) -> Path:
    path = cache_dir / name
    path.write_bytes(b"x" * size)
    os.utime(path, (mtime, mtime))
    return path


def test_prune_over_cap_deletes_oldest_first(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _cache_file(cache_dir, "a.json", 2 * _MIB, 1000.0)
    _cache_file(cache_dir, "b.json", 2 * _MIB, 2000.0)
    _cache_file(cache_dir, "c.json", 2 * _MIB, 3000.0)
    deleted = prune_window_cache(cache_dir, 5)  # 6 MiB total, 5 MiB cap
    assert deleted == 1
    assert not (cache_dir / "a.json").exists()  # the oldest went first
    assert (cache_dir / "b.json").exists()
    assert (cache_dir / "c.json").exists()


def test_prune_under_cap_deletes_nothing(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _cache_file(cache_dir, "a.json", _MIB, 1000.0)
    _cache_file(cache_dir, "b.json", _MIB, 2000.0)
    assert prune_window_cache(cache_dir, 2048) == 0
    assert (cache_dir / "a.json").exists()
    assert (cache_dir / "b.json").exists()


def test_prune_zero_disables_the_cap(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _cache_file(cache_dir, "big.json", 4 * _MIB, 1000.0)
    assert prune_window_cache(cache_dir, 0) == 0
    assert (cache_dir / "big.json").exists()


def test_prune_undeadable_file_does_not_raise(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    blocked = cache_dir / "blocked.json"
    blocked.mkdir()  # a directory shaped like a cache file: unlink fails
    os.utime(blocked, (500.0, 500.0))  # oldest: first in delete order
    _cache_file(cache_dir, "a.json", 2 * _MIB, 1000.0)
    _cache_file(cache_dir, "b.json", 2 * _MIB, 2000.0)
    deleted = prune_window_cache(cache_dir, 1)  # both real files must go
    assert deleted == 2
    assert blocked.exists()
    assert not (cache_dir / "a.json").exists()
    assert not (cache_dir / "b.json").exists()


# ---------------------------------------------------------------------------
# docling_env_warnings (issue #220)
# ---------------------------------------------------------------------------


def _patch_specs(monkeypatch: pytest.MonkeyPatch, present: dict[str, bool]) -> None:
    def fake_find_spec(name: str) -> object | None:
        return object() if present.get(name, False) else None

    monkeypatch.setattr(textbook_pages, "find_spec", fake_find_spec)


def test_env_warning_when_onnxruntime_sits_beside_docling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_specs(monkeypatch, {"docling": True, "onnxruntime": True})
    warnings = textbook_pages.docling_env_warnings()
    assert len(warnings) == 1
    assert "onnxruntime" in warnings[0]
    assert "environment" in warnings[0]


def test_env_warning_empty_without_onnxruntime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_specs(monkeypatch, {"docling": True})
    assert textbook_pages.docling_env_warnings() == []


def test_env_warning_empty_without_docling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_specs(monkeypatch, {"onnxruntime": True})
    assert textbook_pages.docling_env_warnings() == []


# ---------------------------------------------------------------------------
# settings_from: the three new keys
# ---------------------------------------------------------------------------


def test_settings_parse_the_three_new_keys() -> None:
    settings = settings_from(
        {
            "textbook": {
                "window_subprocess": False,
                "window_timeout_s": 12.5,
                "cache_max_mb": 0,
            }
        }
    )
    assert settings.window_subprocess is False
    assert settings.window_timeout_s == 12.5
    assert settings.cache_max_mb == 0


def test_settings_accept_an_int_timeout() -> None:
    settings = settings_from({"textbook": {"window_timeout_s": 60}})
    assert settings.window_timeout_s == 60.0


def test_settings_new_keys_keep_defaults_on_junk() -> None:
    settings = settings_from(
        {
            "textbook": {
                "window_subprocess": 1,
                "window_timeout_s": "long",
                "cache_max_mb": -5,
            }
        }
    )
    assert settings.window_subprocess is True
    assert settings.window_timeout_s == 3600.0
    assert settings.cache_max_mb == 2048


def test_settings_defaults_cover_the_new_keys() -> None:
    defaults = settings_from(None)
    assert defaults.window_subprocess is True
    assert defaults.window_timeout_s == 3600.0
    assert defaults.cache_max_mb == 2048
    assert defaults.structured_output is False


def test_settings_parse_the_structured_output_flag() -> None:
    on = settings_from({"textbook": {"structured_output": True}})
    assert on.structured_output is True
    junk = settings_from({"textbook": {"structured_output": 1}})  # ints are not flags here
    assert junk.structured_output is False
