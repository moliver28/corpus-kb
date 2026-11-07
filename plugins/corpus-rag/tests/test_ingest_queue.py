"""Behavior tests for the study-folder ingest queue (issue #352).

Queue: ``<folder>/.claude/corpus-pending.jsonl`` behind a ``.lock``. One
line per path, dedup on (path, mtime). Drain sends each row, keeps
failures, drops rows for deleted files with no engine call, and keeps
rows added during the run. No test launches a real engine server.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

PLUGIN = str(Path(__file__).resolve().parents[1])
if PLUGIN not in sys.path:
    sys.path.insert(0, PLUGIN)

import ingest_queue  # noqa: E402
from engine_paths import plugin_data_dir  # noqa: E402


def setup_data(tmp_path: Path, monkeypatch) -> Path:
    data = tmp_path / "plugin-data"
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data))
    return data


def make_file(folder: Path, name: str, text: str = "x") -> Path:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class FakeSender:
    """Records op calls; fails on demand. Stands in for the op runner."""

    def __init__(self, fail_names: tuple[str, ...] = (), hook=None) -> None:
        self.calls: list[tuple[str, list[str]]] = []
        self.fail_names = fail_names
        self.hook = hook

    def __call__(self, name: str, pairs: list[str]) -> dict:
        self.calls.append((name, list(pairs)))
        if self.hook is not None:
            self.hook(name, pairs)
        if name in self.fail_names:
            raise RuntimeError(f"op {name} failed")
        return {"status": "success"}


class FakeLogger:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def __call__(self, path: str, route: str, result: str) -> None:
        self.rows.append({"path": path, "route": route, "result": result})


# ---------------------------------------------------------------------------
# Enqueue and dedup
# ---------------------------------------------------------------------------


def test_enqueue_writes_one_row(tmp_path: Path, monkeypatch) -> None:
    folder = tmp_path / "study"
    file = make_file(folder, "notes.md")
    ingest_queue.enqueue(folder, file, kind="file")
    rows = ingest_queue.queue_rows(folder)
    assert len(rows) == 1
    assert rows[0]["path"] == str(file)
    assert rows[0]["kind"] == "file"
    for key in ("mtime", "size", "ts"):
        assert key in rows[0]
    assert (folder / ".claude" / "corpus-pending.jsonl.lock").exists()


def test_enqueue_dedups_same_path_and_mtime(tmp_path: Path) -> None:
    folder = tmp_path / "study"
    file = make_file(folder, "notes.md")
    assert ingest_queue.enqueue(folder, file, kind="file") is True
    assert ingest_queue.enqueue(folder, file, kind="file") is False
    assert len(ingest_queue.queue_rows(folder)) == 1


def test_enqueue_same_path_new_mtime_replaces(tmp_path: Path) -> None:
    folder = tmp_path / "study"
    file = make_file(folder, "notes.md")
    ingest_queue.enqueue(folder, file, kind="file")
    future = time.time() + 100
    import os

    os.utime(file, (future, future))
    ingest_queue.enqueue(folder, file, kind="file")
    rows = ingest_queue.queue_rows(folder)
    assert len(rows) == 1
    assert abs(rows[0]["mtime"] - future) < 1


def test_enqueue_missing_file_is_a_no_op(tmp_path: Path) -> None:
    assert ingest_queue.enqueue(tmp_path, tmp_path / "ghost.md", kind="file") is False


def test_enqueue_infers_transcript_kind(tmp_path: Path) -> None:
    folder = tmp_path / "study"
    make_file(folder, "p1.md", "P1: Hello.\nP2: Hi.\nP1: Next.\nP2: Go.\n")
    make_file(folder, "notes.md", "plain text")
    ingest_queue.enqueue(folder, folder / "p1.md")
    ingest_queue.enqueue(folder, folder / "notes.md")
    rows = {row["path"]: row["kind"] for row in ingest_queue.queue_rows(folder)}
    assert rows[str(folder / "p1.md")] == "transcript"
    assert rows[str(folder / "notes.md")] == "file"


# ---------------------------------------------------------------------------
# Drain routing and outcomes
# ---------------------------------------------------------------------------


def run_drain(folder: Path, sender, logger) -> dict:
    return ingest_queue.drain(folder, "q3", sender=sender, logger=logger)


def test_drain_transcript_takes_two_ops(tmp_path: Path) -> None:
    folder = tmp_path / "study"
    file = make_file(folder, "p1.md", "P1: Hello.\nP2: Hi.\nP1: Next.\nP2: Go.\n")
    ingest_queue.enqueue(folder, file)
    sender, logger = FakeSender(), FakeLogger()
    report = run_drain(folder, sender, logger)
    assert [name for name, _pairs in sender.calls] == [
        "research_ingest_transcript",
        "ingest_file",
    ]
    assert sender.calls[0][1] == [f"file_path={file}"]
    assert report == {"ingested": 1, "failed": 0, "dropped": 0}
    assert ingest_queue.queue_rows(folder) == []
    assert logger.rows[0]["route"] == "research_ingest_transcript+ingest_file"
    assert logger.rows[0]["result"] == "ok"


def test_drain_plain_file_takes_queue_enqueue(tmp_path: Path) -> None:
    folder = tmp_path / "study"
    file = make_file(folder, "notes.md")
    ingest_queue.enqueue(folder, file, kind="file")
    sender, logger = FakeSender(), FakeLogger()
    run_drain(folder, sender, logger)
    assert [name for name, _pairs in sender.calls] == ["queue_enqueue"]
    assert sender.calls[0][1] == [f"path={file}"]


def test_drain_drops_deleted_files_without_engine_call(tmp_path: Path) -> None:
    folder = tmp_path / "study"
    file = make_file(folder, "gone.md")
    ingest_queue.enqueue(folder, file, kind="file")
    file.unlink()
    sender, logger = FakeSender(), FakeLogger()
    report = run_drain(folder, sender, logger)
    assert sender.calls == []
    assert report["dropped"] == 1
    assert ingest_queue.queue_rows(folder) == []
    assert logger.rows[0]["result"] == "dropped"


def test_drain_keeps_failed_rows(tmp_path: Path) -> None:
    folder = tmp_path / "study"
    file = make_file(folder, "notes.md")
    ingest_queue.enqueue(folder, file, kind="file")
    sender, logger = FakeSender(fail_names=("queue_enqueue",)), FakeLogger()
    report = run_drain(folder, sender, logger)
    assert report["failed"] == 1
    rows = ingest_queue.queue_rows(folder)
    assert len(rows) == 1
    assert rows[0]["path"] == str(file)
    assert "failed" in logger.rows[0]["result"]


def test_drain_keeps_rows_added_during_the_run(tmp_path: Path) -> None:
    folder = tmp_path / "study"
    first = make_file(folder, "first.md")
    ingest_queue.enqueue(folder, first, kind="file")
    late = make_file(folder, "late.md")

    def add_during_run(name: str, pairs: list[str]) -> None:
        ingest_queue.enqueue(folder, late, kind="file")

    sender = FakeSender(hook=add_during_run)
    run_drain(folder, sender, FakeLogger())
    rows = ingest_queue.queue_rows(folder)
    assert [row["path"] for row in rows] == [str(late)]


def test_drain_processes_all_rows(tmp_path: Path) -> None:
    folder = tmp_path / "study"
    for name in ("a.md", "b.md", "c.md"):
        ingest_queue.enqueue(folder, make_file(folder, name), kind="file")
    sender, logger = FakeSender(), FakeLogger()
    report = run_drain(folder, sender, logger)
    assert report["ingested"] == 3
    assert len(sender.calls) == 3


# ---------------------------------------------------------------------------
# Ingest log
# ---------------------------------------------------------------------------


def test_ingest_log_rows(tmp_path: Path, monkeypatch) -> None:
    setup_data(tmp_path, monkeypatch)
    ingest_queue.log_ingest("q3", str(tmp_path / "a.md"), "queue_enqueue", "ok")
    ingest_queue.log_ingest("q3", str(tmp_path / "b.md"), "queue_enqueue", "failed: boom")
    path = plugin_data_dir() / "ingest-log" / "q3.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["route"] == "queue_enqueue"
    assert rows[0]["result"] == "ok"
    assert rows[1]["result"] == "failed: boom"
    for row in rows:
        assert set(row) == {"ts", "path", "route", "result"}


def test_drain_writes_the_ingest_log(tmp_path: Path, monkeypatch) -> None:
    data = setup_data(tmp_path, monkeypatch)
    folder = tmp_path / "study"
    ingest_queue.enqueue(folder, make_file(folder, "notes.md"), kind="file")
    sender = FakeSender()

    def logger(path: str, route: str, result: str) -> None:
        ingest_queue.log_ingest("q3", path, route, result)

    run_drain(folder, sender, logger)
    path = data / "ingest-log" / "q3.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["result"] == "ok"


def test_queue_skips_malformed_lines(tmp_path: Path) -> None:
    folder = tmp_path / "study"
    queue = folder / ".claude" / "corpus-pending.jsonl"
    queue.parent.mkdir(parents=True)
    queue.write_text("{broken\n", encoding="utf-8")
    assert ingest_queue.queue_rows(folder) == []
