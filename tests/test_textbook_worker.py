"""Tests for the persistent parser worker (issue #270).

The process boundary is patched at the ``_start_worker`` seam inside
``textbook_worker``, so no test starts a real child worker here. The
fakes script each worker response, so the tests pin reuse, recycle,
crash, timeout, and worker-reported-failure paths, plus the
determinism contract: a reused window carries the same items the
per-window child ships, and the built chunks do not change with reuse
on or off. One opt-in live check runs a real docling child when the
``[docling]`` extra is installed and skips like the other
optional-extra tests when it is not.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from queue import Empty
from typing import ClassVar

import pytest

from corpus_kb.ingestion.base import ChunkPlan, SourceProfile
from corpus_kb.ingestion.strategies import (
    textbook,
    textbook_subprocess,
    textbook_windows,
    textbook_worker,
)
from corpus_kb.ingestion.strategies.textbook import TEXTBOOK_STRATEGY_NAME, TextbookStrategy
from corpus_kb.ingestion.strategies.textbook_docling import PageItem
from corpus_kb.ingestion.strategies.textbook_worker import (
    _WorkerHandle,
    parse_window_with_worker,
)

_ROWS: list[dict[str, object]] = [
    {"page": 1, "label": "text", "text": "one"},
    {"page": 2, "label": "text", "text": "two"},
]


# ---------------------------------------------------------------------------
# Fakes for the spawn seam (no real child worker ever starts)
# ---------------------------------------------------------------------------


class _FakeProc:
    """BaseProcess double: exitcode control plus terminate/join tracking."""

    def __init__(self) -> None:
        self.exitcode: int | None = None
        self.terminated = False
        self.killed = False

    def join(self, timeout: float | None = None) -> None:
        del timeout

    def is_alive(self) -> bool:
        if self.exitcode is not None or self.killed:
            return False
        return not self.terminated

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


class _RequestQueue:
    """Records what the parent sends; ``close`` marks the handle dropped."""

    def __init__(self) -> None:
        self.requests: list[object] = []
        self.closed = False

    def put(self, request: object) -> None:
        self.requests.append(request)

    def close(self) -> None:
        self.closed = True


class _ScriptedQueue:
    """Payload-queue double: pops one scripted outcome per read.

    Outcomes: a rows list, ``None`` (a worker-reported window failure),
    ``"empty"`` (no response; the timeout path), and ``"crash"`` (the
    worker dies mid-window, so no response ever arrives).
    """

    def __init__(self, script: list[object], proc: _FakeProc) -> None:
        self._script = script  # shared: one script feeds every spawned worker
        self._proc = proc
        self._dead = False
        self.closed = False

    def get(self, timeout: float | None = None) -> list[object] | None:
        del timeout
        outcome = self._script.pop(0) if self._script else "empty"
        if outcome == "crash":
            self._proc.exitcode = 1
            self._dead = True
        if self._dead or outcome == "empty":
            raise Empty
        return outcome  # a rows list, or None (a failed window)

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _fresh_worker_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clean worker table per test, so module state never leaks."""
    monkeypatch.setattr(textbook_worker, "_WORKERS", {})


def _patch_start_worker(
    monkeypatch: pytest.MonkeyPatch,
    spawns: list[str],
    scripts: list[list[object]],
    handles: list[_WorkerHandle] | None = None,
) -> None:
    """Worker N drains ``scripts[N]`` (the last script covers extra spawns)."""

    def fake_start(parser: str) -> _WorkerHandle:
        spawns.append(parser)
        script = scripts[min(len(spawns) - 1, len(scripts) - 1)]
        proc = _FakeProc()
        handle = _WorkerHandle(
            parser=parser,
            proc=proc,
            requests=_RequestQueue(),
            payload=_ScriptedQueue(script, proc),
        )
        if handles is not None:
            handles.append(handle)
        return handle

    monkeypatch.setattr(textbook_worker, "_start_worker", fake_start)


def _parse(start: int, end: int, timeout_s: float = 5.0, **kwargs: object) -> list[PageItem] | None:
    return parse_window_with_worker(Path("book.pdf"), "docling", start, end, timeout_s, **kwargs)


# ---------------------------------------------------------------------------
# Reuse, recycle, crash, timeout, and failure paths
# ---------------------------------------------------------------------------


def test_one_worker_serves_every_window(monkeypatch: pytest.MonkeyPatch) -> None:
    spawns: list[str] = []
    handles: list[_WorkerHandle] = []
    _patch_start_worker(monkeypatch, spawns, [[_ROWS, _ROWS, _ROWS]], handles)
    first = _parse(1, 2)
    second = _parse(3, 4)
    third = _parse(5, 6)
    assert spawns == ["docling"]  # one worker, three windows
    assert [item.text for item in first] == ["one", "two"]
    assert [item.page for item in second] == [1, 2]
    assert [item.page for item in third] == [1, 2]
    handle = handles[0]
    assert handle.requests.requests == [
        ("book.pdf", 1, 2, False),
        ("book.pdf", 3, 4, False),
        ("book.pdf", 5, 6, False),
    ]
    assert handle.windows_served == 3
    assert textbook_worker._WORKERS["docling"] is handle  # the worker stays


def test_a_worker_past_max_windows_is_recycled(monkeypatch: pytest.MonkeyPatch) -> None:
    spawns: list[str] = []
    handles: list[_WorkerHandle] = []
    _patch_start_worker(monkeypatch, spawns, [[_ROWS, _ROWS], [_ROWS]], handles)
    assert _parse(1, 2, max_windows=2) is not None  # worker one serves
    assert _parse(3, 4, max_windows=2) is not None  # the served count hits the cap
    assert _parse(5, 6, max_windows=2) is not None  # stale: the worker recycles
    assert spawns == ["docling", "docling"]
    assert handles[0].proc.terminated  # the old worker was stopped
    assert handles[0].requests.closed and handles[0].payload.closed
    fresh = textbook_worker._WORKERS["docling"]
    assert fresh is handles[1]
    assert fresh.windows_served == 1
    assert fresh.requests.requests == [("book.pdf", 5, 6, False)]


def test_a_crashed_worker_is_replaced_for_the_next_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawns: list[str] = []
    handles: list[_WorkerHandle] = []
    _patch_start_worker(monkeypatch, spawns, [[_ROWS, "crash"], [_ROWS]], handles)
    assert _parse(1, 2) is not None  # worker one serves
    assert _parse(3, 4) is None  # crash mid-window: this window falls back
    assert "docling" not in textbook_worker._WORKERS  # the table dropped the dead handle
    assert handles[0].requests.closed and handles[0].payload.closed
    assert spawns == ["docling"]  # nothing respawns until the next window asks
    assert _parse(5, 6) is not None  # the next window spawned a fresh worker
    assert spawns == ["docling", "docling"]  # exactly one respawn
    assert textbook_worker._WORKERS["docling"] is handles[1]


def test_a_window_past_its_deadline_stops_the_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawns: list[str] = []
    handles: list[_WorkerHandle] = []
    _patch_start_worker(monkeypatch, spawns, [[], [_ROWS]], handles)
    assert _parse(1, 2, timeout_s=0.05) is None
    handle = handles[0]
    assert handle.proc.terminated  # the stuck worker was stopped
    assert handle.requests.closed and handle.payload.closed
    assert "docling" not in textbook_worker._WORKERS  # the table dropped it
    assert _parse(3, 4) is not None  # the next window spawns a fresh worker
    assert spawns == ["docling", "docling"]


def test_a_worker_reported_failure_keeps_the_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawns: list[str] = []
    handles: list[_WorkerHandle] = []
    _patch_start_worker(monkeypatch, spawns, [[None, _ROWS]], handles)
    assert _parse(1, 2) is None  # the worker shipped None rows: the window fell back
    assert spawns == ["docling"]  # the loaded worker stays
    handle = handles[0]
    assert handle.windows_served == 1
    assert not handle.requests.closed
    assert _parse(3, 4) is not None  # the same worker serves the next window
    assert spawns == ["docling"]


# ---------------------------------------------------------------------------
# The determinism contract: reuse changes speed, never output
# ---------------------------------------------------------------------------


def _patch_legacy_child(
    monkeypatch: pytest.MonkeyPatch,
    children: list[tuple[int, int]],
    rows_for: Callable[[int, int], list[dict[str, object]]],
) -> None:
    """One legacy child per call; it ships the rows and exits clean."""

    class _LegacyProc:
        exitcode = 0

        def join(self, timeout: float | None = None) -> None:
            del timeout

        def is_alive(self) -> bool:
            return False

    class _OneShotQueue:
        def __init__(self, rows: list[dict[str, object]]) -> None:
            self._rows = rows
            self.closed = False

        def get(self, timeout: float | None = None) -> list[dict[str, object]]:
            del timeout
            if self._rows is None:
                raise Empty
            rows, self._rows = self._rows, None
            return rows

        def close(self) -> None:
            self.closed = True

    def fake_start_child(
        pdf_path: str,
        parser: str,
        start: int,
        end: int,
        structured: bool = False,
    ) -> tuple[_LegacyProc, _OneShotQueue]:
        del pdf_path, parser, structured
        children.append((start, end))
        return _LegacyProc(), _OneShotQueue(rows_for(start, end))

    monkeypatch.setattr(
        "corpus_kb.ingestion.strategies.textbook_subprocess._start_child", fake_start_child
    )


def test_reused_windows_match_the_spawn_per_window_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same window through both seams ships the same items."""

    def rows_for(start: int, end: int) -> list[dict[str, object]]:
        return [
            {"page": start, "label": "text", "text": f"page {start} body"},
            {"page": end, "label": "section_header", "text": f"Part {end}"},
        ]

    spawns: list[str] = []
    _patch_start_worker(monkeypatch, spawns, [[rows_for(1, 2), rows_for(3, 4)]])
    children: list[tuple[int, int]] = []
    _patch_legacy_child(monkeypatch, children, rows_for)
    legacy = [
        textbook_subprocess.parse_window_in_subprocess(Path("book.pdf"), "docling", 1, 2, 5.0),
        textbook_subprocess.parse_window_in_subprocess(Path("book.pdf"), "docling", 3, 4, 5.0),
    ]
    reused = [
        parse_window_with_worker(Path("book.pdf"), "docling", 1, 2, 5.0),
        parse_window_with_worker(Path("book.pdf"), "docling", 3, 4, 5.0),
    ]
    assert legacy == reused  # byte-stable output across the two child seams
    assert len(children) == 2  # the legacy path spawned one child per window
    assert spawns == ["docling"]  # the worker path spawned once for both


def test_chunks_do_not_change_with_reuse_off_or_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Full-strategy determinism: reuse off and on build identical chunks.

    The reader and the parser probes are faked, so this runs with no
    optional extras: only the child seams differ between the two runs.
    """

    def rows_for(start: int, end: int) -> list[dict[str, object]]:
        return [
            {"page": page, "label": "section_header", "text": f"Chapter {page}"}
            if page == start
            else {"page": page, "label": "text", "text": f"page {page} body"}
            for page in range(start, end + 1)
        ]

    class _FakeReader:
        pages: ClassVar[list[object]] = [object(), object(), object(), object()]

    monkeypatch.setattr(textbook, "open_pdf", lambda path: (_FakeReader(), None))
    monkeypatch.setattr(textbook, "read_outline", lambda reader: [])
    monkeypatch.setattr(textbook_windows, "find_spec", lambda name: object())
    spawns: list[str] = []
    _patch_start_worker(monkeypatch, spawns, [[rows_for(1, 2), rows_for(3, 4)]])
    children: list[tuple[int, int]] = []
    _patch_legacy_child(monkeypatch, children, rows_for)
    source = tmp_path / "book.pdf"  # the bytes only feed the sha256 cache key
    source.write_bytes(b"a stub the fake reader never parses")

    def build(cache: Path, reuse: bool) -> ChunkPlan:
        config = {
            "ingestion": {"strategies": {}, "default": "default"},
            "textbook": {
                "min_pages": 1,
                "window_pages": 2,
                "cache_dir": str(cache),
                "worker_reuse": reuse,
            },
        }
        profile = SourceProfile(
            source_type="textbook", is_file=True, path=str(source), config=config
        )
        return TextbookStrategy().build_chunks("", "book.pdf", profile)

    off = build(tmp_path / "cache-off", False)
    on = build(tmp_path / "cache-on", True)
    assert off.strategy == on.strategy == TEXTBOOK_STRATEGY_NAME
    assert off.warnings == on.warnings == []  # every window was served, none fell back
    assert [(chunk.text, dict(chunk.metadata)) for chunk in off.chunks] == [
        (chunk.text, dict(chunk.metadata)) for chunk in on.chunks
    ]
    assert off.document_text == on.document_text
    assert off.chunks  # the fake pages really built chunks
    assert len(children) == 2  # reuse off: one child per window
    assert spawns == ["docling"]  # reuse on: one worker for both windows


# ---------------------------------------------------------------------------
# Opt-in live check: one real docling child, two windows
# ---------------------------------------------------------------------------


def _two_page_pdf(path: Path) -> Path:
    """A hand-assembled two-page PDF: one text line per page.

    The object layout and the xref follow the ``_minimal_pdf`` shape
    the strategy tests use, so real parsers accept the file.
    """
    texts = ["Page one body text", "Page two body text"]
    objects: list[bytes] = []
    count = len(texts)
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(count))
    font_object = 3 + 2 * count
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {count} >>".encode())
    for i, text in enumerate(texts):
        content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /Contents {4 + 2 * i} 0 R "
            f"/Resources << /Font << /F1 {font_object} 0 R >> >> >>".encode()
        )
        stream = f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream"
        objects.append(stream)
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n".encode()
    )
    path.write_bytes(bytes(out))
    return path


def test_live_docling_worker_serves_two_windows_from_one_process(tmp_path: Path) -> None:
    """Opt-in live check: a real docling child serves both windows.

    Skipped without the ``[docling]`` extra, like the other
    optional-extra tests. This is the one test here that starts a real
    spawned child process.
    """
    pytest.importorskip("docling")
    path = _two_page_pdf(tmp_path / "live.pdf")
    first: list[PageItem] | None = None
    second: list[PageItem] | None = None
    try:
        first = parse_window_with_worker(path, "docling", 1, 1, 900.0)
        handle = textbook_worker._WORKERS.get("docling")
        assert handle is not None
        assert handle.proc.exitcode is None  # the worker stayed alive after window one
        second = parse_window_with_worker(path, "docling", 2, 2, 900.0)
        assert handle.windows_served == 2  # one worker served both windows
        assert textbook_worker._WORKERS.get("docling") is handle
    finally:
        live = textbook_worker._WORKERS.get("docling")
        if live is not None:
            textbook_worker._discard_worker("docling", live)
    assert first  # the real parse recovered items from the text line
    assert [item.page for item in first] == [1]
    assert second
    assert [item.page for item in second] == [2]
