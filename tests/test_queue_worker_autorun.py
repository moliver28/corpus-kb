"""The server autorun drain (textbook program Wave 3): task lifecycle.

Offline pins: off (the default) starts no task; on starts ONE drain task
over the same QueueWorker loop and stops it cleanly through the worker's
stop flag; a DuckDB refusal answers None instead of a task; the logged
exception consumer ends the task quietly; the run_all wiring call sites
are pinned by source inspection (the uvicorn seam is heavy).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from corpus_kb.queue.autorun import QueueAutorun, _drain_forever, start_queue_autorun
from tests.cli_fakes import TENANT, FakeQueuePool, queue_row

RUN_ID = "00000000-0000-0000-0000-0000000000f1"

SERVER_WIRING = (
    Path(__file__).resolve().parents[1] / "src" / "corpus_kb" / "server_wiring.py"
).read_text(encoding="utf-8")


def _config(**queue: Any) -> dict[str, Any]:
    queue_cfg: dict[str, Any] = {"poll_seconds": 0.01, "stale_lease_seconds": 600, **queue}
    return {"database": {"backend": "postgres"}, "queue": queue_cfg}


def _patch_ingest(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(pool: Any, config: Any, tenant_id: str, path: str) -> dict[str, Any]:
        return {"status": "success", "run_id": RUN_ID}

    monkeypatch.setattr("corpus_kb.queue.worker.ingest_book", fake)


def test_autorun_off_starts_nothing() -> None:
    assert start_queue_autorun(FakeQueuePool(), _config()) is None
    assert start_queue_autorun(FakeQueuePool(), _config(autorun=False)) is None


@pytest.mark.asyncio
async def test_autorun_on_starts_one_task_and_stops_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = FakeQueuePool([queue_row()])
    _patch_ingest(monkeypatch)
    autorun = start_queue_autorun(pool, _config(autorun=True), tenant_id=TENANT)
    assert isinstance(autorun, QueueAutorun)
    for _ in range(50):  # one bounded wait: the drain task picks up the row
        if pool.rows[0]["status"] != "pending":
            break
        await asyncio.sleep(0.01)
    assert pool.rows[0]["status"] in ("running", "done")
    await autorun.stop()
    assert autorun.task.done()
    assert autorun.task.exception() is None
    assert autorun.worker.stop.requested
    assert autorun.worker.stop.reason == "server shutdown"
    assert pool.rows[0]["status"] == "done"


def test_autorun_duckdb_refusal_stays_none() -> None:
    cfg: dict[str, Any] = {
        "database": {"backend": "duckdb"},
        "queue": {"autorun": True, "poll_seconds": 0.01},
    }
    assert start_queue_autorun(FakeQueuePool(), cfg) is None


@pytest.mark.asyncio
async def test_the_drain_consumer_ends_quietly_on_an_error() -> None:
    class _FailingWorker:
        def request_stop(self, reason: str = "stop requested") -> None:
            return None

        async def watch(self) -> dict[str, Any]:
            raise RuntimeError("pool vanished")

    worker = _FailingWorker()
    task = asyncio.create_task(_drain_forever(worker))
    await task
    assert task.exception() is None  # the consumer logged it; nothing escapes


def test_run_all_starts_and_stops_the_autorun() -> None:
    # Source-inspection pin (the uvicorn seam is too heavy for the unit
    # tier): run_all starts the drain after the browser task and stops it
    # first in the shutdown path, before projections and the pool close.
    assert 'start_queue_autorun(services["pool"], config)' in SERVER_WIRING
    assert "await queue_autorun.stop()" in SERVER_WIRING
