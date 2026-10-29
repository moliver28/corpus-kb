"""The ``queue-run`` CLI leg: honest exit codes and the drain report.

Offline by construction: the config/pool seam (cli_ingest) and the
pipeline seam (queue.worker.ingest_book) are faked, so the exit codes,
the human rendering, and the ``--json`` envelope run end to end.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from corpus_kb.cli import app
from tests.cli_fakes import FakeQueuePool, queue_row

runner = CliRunner()

RUN_ID = "00000000-0000-0000-0000-0000000000f1"


def _patch_world(
    monkeypatch: pytest.MonkeyPatch, pool: FakeQueuePool, over: dict[str, Any] | None = None
) -> None:
    async def _open(cfg: dict[str, object]) -> FakeQueuePool:
        return pool

    def _cfg() -> dict[str, Any]:
        config: dict[str, Any] = {
            "database": {"backend": "postgres", "connection_string": "postgres:///x"},
            "queue": {"poll_seconds": 20, "stale_lease_seconds": 600, "max_attempts": 3},
        }
        config.update(over or {})
        return config

    monkeypatch.setattr("corpus_kb.cli.cli_ingest._open_pool", _open)
    monkeypatch.setattr("corpus_kb.cli.cli_ingest._load_config", _cfg)


def _patch_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(pool: Any, config: Any, tenant_id: str, path: str) -> dict[str, Any]:
        return {"status": "success", "run_id": RUN_ID, "pg_chunk_count": 5}

    monkeypatch.setattr("corpus_kb.queue.worker.ingest_book", fake)


def test_queue_run_drains_and_exits_zero(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    pool = FakeQueuePool([queue_row(path=str(tmp_path / "book.pdf"))])
    _patch_world(monkeypatch, pool)
    _patch_success(monkeypatch)
    result = runner.invoke(app, ["queue-run", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "success"
    assert payload["counts"] == {"done": 1, "failed": 0, "pending": 0}
    assert payload["rows"][0]["status"] == "done"
    assert payload["rows"][0]["run_id"] == RUN_ID
    assert pool.closed is True


def test_queue_run_prints_the_human_report(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = FakeQueuePool([queue_row()])
    _patch_world(monkeypatch, pool)
    _patch_success(monkeypatch)
    result = runner.invoke(app, ["queue-run"])
    assert result.exit_code == 0
    assert "queue: 1 rows handled" in result.output
    assert "done 1, failed 0, pending 0" in result.output
    assert "attempts=0" in result.output


def test_queue_run_exits_one_when_a_row_lands_on_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = FakeQueuePool([queue_row(attempts=2, max_attempts=3)])
    _patch_world(monkeypatch, pool)

    async def fake(pool: Any, config: Any, tenant_id: str, path: str) -> dict[str, Any]:
        raise RuntimeError("boom")

    monkeypatch.setattr("corpus_kb.queue.worker.ingest_book", fake)
    result = runner.invoke(app, ["queue-run"])
    assert result.exit_code == 1
    assert "failed 1" in result.output
    assert "boom" in result.output


def test_queue_run_exits_zero_when_a_retry_goes_back_to_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # max_attempts 5: one pass spends 3 tries and the row is still pending.
    pool = FakeQueuePool([queue_row(max_attempts=5)])
    _patch_world(monkeypatch, pool)

    async def fake(pool: Any, config: Any, tenant_id: str, path: str) -> dict[str, Any]:
        return {"status": "error", "message": "File not found"}

    monkeypatch.setattr("corpus_kb.queue.worker.ingest_book", fake)
    result = runner.invoke(app, ["queue-run"])
    assert result.exit_code == 0
    assert "pending 1" in result.output


def test_queue_run_refuses_the_duckdb_backend_typed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = FakeQueuePool()

    async def _open(cfg: dict[str, object]) -> FakeQueuePool:
        return pool

    def _cfg() -> dict[str, Any]:
        return {"database": {"backend": "duckdb", "connection_string": "duckdb:///x.duckdb"}}

    monkeypatch.setattr("corpus_kb.cli.cli_ingest._open_pool", _open)
    monkeypatch.setattr("corpus_kb.cli.cli_ingest._load_config", _cfg)
    result = runner.invoke(app, ["queue-run"])
    assert result.exit_code == 3
    assert "ERROR" in result.output
    assert "Postgres" in result.output


def test_queue_run_watch_ends_on_the_stop(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    pool = FakeQueuePool([queue_row(path=str(tmp_path / "book.pdf"))])
    _patch_world(monkeypatch, pool, {"queue": {"poll_seconds": 0.01, "stale_lease_seconds": 600}})
    captured: dict[str, Any] = {}

    def _capture(flag: Any) -> None:
        captured["flag"] = flag

    # The stop lands while the first book runs: watch ends after pass one.
    monkeypatch.setattr("corpus_kb.cli.cli_queue.install_stop_signals", _capture)

    async def fake(pool: Any, config: Any, tenant_id: str, path: str) -> dict[str, Any]:
        captured["flag"].request("test stop")
        return {"status": "success", "run_id": RUN_ID}

    monkeypatch.setattr("corpus_kb.queue.worker.ingest_book", fake)
    result = runner.invoke(app, ["queue-run", "--watch", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["counts"]["done"] == 1
    assert payload["stopped"] is True
