"""Issue #88 hardening for the ingest watch loop.

Covers three findings: (1) recursive scan parity — the watch pass walks the
SAME ``rglob`` + suffix filter as the one-shot directory ingest; (2) per-file
crash isolation — an unexpected exception out of ``ingest_file`` is logged,
counted in the summary's ``failed``, and the loop continues while
``CancelledError`` still propagates; (3) PostgresError exit-code parity —
isolated mid-loop (log, count, continue, never heal-retried), exit 3 at
watcher startup through the real CLI wrapper.

Offline by construction: the pipeline edge is a fake at the SAME
``ingest_file`` seam the CLI wiring uses, and the startup test fakes
``cli_ingest._open_pool`` (no database, no Ollama).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import asyncpg
import pytest
from typer.testing import CliRunner

from corpus_kb.cli import app
from corpus_kb.cli.cli_ingest import _resolve_files
from corpus_kb.ingest_watch import (
    WatchOptions,
    scan_watch_dir,
    watch_directory,
)

runner = CliRunner()


class _Recorder:
    """Injected sleeper/clock/report doubles (the test_cli_ingest shape)."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []
        self.messages: list[str] = []
        self.tick = 0.0

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.tick += seconds

    def clock(self) -> float:
        return self.tick

    def report(self, message: str) -> None:
        self.messages.append(message)


class _ScriptedCalls:
    """Per-path outcome queues: dict results are returned, exceptions raised.

    The last queue item repeats (stable across re-scans), matching the
    canned-result style the one-shot watch tests use.
    """

    def __init__(self, script: dict[str, list[dict[str, object] | Exception]]) -> None:
        self.script = script
        self.calls: list[Path] = []

    async def __call__(self, path: Path) -> dict[str, object]:
        self.calls.append(path)
        queue = self.script[str(path)]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, BaseException):  # CancelledError is BaseException, not Exception
            raise item
        return item


def _result(status: str, path: str, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": status,
        "path": path,
        "chunk_count": 1,
        "entity_count": 0,
    }
    payload.update(extra)
    return payload


def _run(coro: object) -> dict[str, object]:
    return cast("dict[str, object]", asyncio.run(coro))


def _watch(
    tmp_path: Path,
    ingest: _ScriptedCalls,
    rec: _Recorder,
    probe_results: list[bool],
    *,
    max_passes: int = 2,
    heal: bool = True,
) -> object:
    probes = iter(probe_results)

    async def probe() -> bool:
        return next(probes, probe_results[-1])

    options = WatchOptions(
        interval_s=10.0,
        heal=heal,
        heal_max_attempts=3,
        heal_backoff_s=5.0,
        max_passes=max_passes,
    )
    return watch_directory(
        tmp_path,
        ingest,
        options,
        probe=probe,
        sleeper=rec.sleep,
        clock=rec.clock,
        report=rec.report,
    )


# ---------------------------------------------------------------------------
# Finding 1: recursive scan parity with the one-shot directory walk
# ---------------------------------------------------------------------------


def test_scan_watch_dir_walks_subtrees_with_one_shot_filter(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    sub = tmp_path / "sub"
    deep = sub / "deep"
    deep.mkdir(parents=True)
    (sub / "b.py").write_text("x", encoding="utf-8")
    (deep / "c.txt").write_text("x", encoding="utf-8")
    (deep / "notes.log").write_text("x", encoding="utf-8")
    (sub / "binary.xyz").write_text("x", encoding="utf-8")
    (deep / "upper.MD").write_text("x", encoding="utf-8")

    names = [p.name for p in scan_watch_dir(tmp_path)]
    assert names == ["a.md", "b.py", "c.txt", "upper.MD"]  # subtree walked, filter applied
    # Exact parity: watch and one-shot agree on the ingestable file set.
    assert scan_watch_dir(tmp_path) == _resolve_files(tmp_path)


def test_scan_watch_dir_matches_one_shot_on_fresh_and_nested(tmp_path: Path) -> None:
    for layout in ({}, {"nested/keep.md": "x", "nested/skip.bin": "x"}):
        for rel, content in layout.items():
            target = tmp_path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(content), encoding="utf-8")
        assert scan_watch_dir(tmp_path) == _resolve_files(tmp_path), layout


# ---------------------------------------------------------------------------
# Finding 2: per-file crash isolation (failed count, CancelledError escapes)
# ---------------------------------------------------------------------------


def test_watch_isolates_crashing_file_and_counts_failed(tmp_path: Path) -> None:
    good = tmp_path / "good.md"
    bad = tmp_path / "bad.md"
    good.write_text("ok", encoding="utf-8")
    bad.write_text("boom", encoding="utf-8")
    ingest = _ScriptedCalls(
        {
            str(good): [_result("success", "good.md")],
            str(bad): [RuntimeError("transient db blip")],
        }
    )
    rec = _Recorder()
    summary = _run(_watch(tmp_path, ingest, rec, probe_results=[True], max_passes=1, heal=False))
    assert summary["failed"] == 1
    assert summary["ingested"] == 1
    assert summary["status"] == "partial"
    assert summary["still_broken"] == [str(bad)]
    # The loop continued past the crash: the good file was still ingested.
    assert str(good) in [str(p) for p in ingest.calls]
    # Honest narration: path + exception, in the module's report style.
    assert any("bad.md" in m and "RuntimeError: transient db blip" in m for m in rec.messages)


def test_watch_cancelled_error_still_propagates(tmp_path: Path) -> None:
    target = tmp_path / "note.md"
    target.write_text("x", encoding="utf-8")
    ingest = _ScriptedCalls({str(target): [asyncio.CancelledError()]})
    rec = _Recorder()
    with pytest.raises(asyncio.CancelledError):
        _run(_watch(tmp_path, ingest, rec, probe_results=[True], max_passes=1, heal=False))


def test_watch_heal_reingest_crash_is_isolated(tmp_path: Path) -> None:
    """A raise during the heal re-ingest must not kill the watcher either."""
    target = tmp_path / "call.txt"
    target.write_text("transcript", encoding="utf-8")
    outage = _result(
        "partial",
        "call.txt",
        errors=["EmbeddingError: Connection failed: ollama returned zero vectors"],
    )
    ingest = _ScriptedCalls(
        {str(target): [outage, asyncpg.PostgresError("connection terminated mid-heal")]}
    )
    rec = _Recorder()
    summary = _run(_watch(tmp_path, ingest, rec, probe_results=[True], max_passes=1))
    assert summary["healed"] == 0
    # failed counts every non-success attempt: the initial partial result
    # (pipeline-reported error) plus the heal re-ingest crash.
    assert summary["failed"] == 2
    assert summary["status"] == "partial"
    assert summary["still_broken"] == [str(target)]
    assert any("connection terminated mid-heal" in m for m in rec.messages)
    assert rec.sleeps == [5.0]  # the heal pass ran (backoff), then isolated the crash


# ---------------------------------------------------------------------------
# Finding 3: PostgresError exit-code parity (mid-loop isolated, startup exits 3)
# ---------------------------------------------------------------------------


def test_watch_postgres_error_mid_loop_isolated_and_never_healed(tmp_path: Path) -> None:
    good = tmp_path / "good.md"
    bad = tmp_path / "bad.md"
    good.write_text("ok", encoding="utf-8")
    bad.write_text("boom", encoding="utf-8")
    ingest = _ScriptedCalls(
        {
            str(good): [_result("success", "good.md")],
            str(bad): [asyncpg.PostgresError("write failed: relation missing")],
        }
    )
    rec = _Recorder()
    summary = _run(_watch(tmp_path, ingest, rec, probe_results=[True] * 5, max_passes=2))
    assert summary["failed"] == 2  # isolated in BOTH passes, watcher kept running
    assert summary["ingested"] == 2
    assert summary["status"] == "partial"
    assert summary["still_broken"] == [str(bad)]
    # The auto-heal set is embedder-outage scoped: PostgresError is counted and
    # reported, never retried by the heal pass (exactly 1 scan call per pass).
    calls_per_path = [str(p) for p in ingest.calls]
    assert calls_per_path.count(str(bad)) == 2
    assert calls_per_path.count(str(good)) == 2


def test_watch_postgres_error_at_startup_exits_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Startup (pool open, before the loop) propagates: exit 3 like one-shot."""
    note = tmp_path / "note.md"
    note.write_text("hello", encoding="utf-8")

    async def _open(_cfg: dict[str, object]) -> object:
        raise asyncpg.PostgresError("password authentication failed for user")

    monkeypatch.setattr("corpus_kb.cli.cli_ingest._open_pool", _open)
    result = runner.invoke(app, ["ingest", str(note), "--watch"])
    assert result.exit_code == 3, result.output
    assert "cannot reach the database" in result.output
