"""The queue worker (textbook program W2C): state machine over fakes.

Offline tier: the pool is the in-memory ``FakeQueuePool`` (cli_fakes) and
the pipeline seam is ``queue.worker.ingest_book``, monkeypatched. Pins:

* claim order (queued_at, id) and the done shape (run id, chunk count)
* the attempt ladder (pending under max_attempts, else failed)
* stale reclaim without an attempt bump; release on stop-before-run
* stop-during-book finishes the book; a lost lease reads pending
* the duckdb backend refusal and the stop-flag signal path
"""

from __future__ import annotations

import asyncio
import signal
from datetime import timedelta
from typing import Any

import pytest

from corpus_kb.errors import ConfigError
from corpus_kb.queue.worker import QueueWorker
from corpus_kb.queue.worker_support import StopFlag, WorkerIdentity, install_stop_signals
from tests.cli_fakes import EPOCH, QUEUE_ID, TENANT, FakeQueuePool, queue_row

IDENT = WorkerIdentity(host="test-host", pid=4242, token="t0")
RUN_ID = "00000000-0000-0000-0000-0000000000f1"
SECOND_ID = "00000000-0000-0000-0000-0000000000e2"


def _config(**queue: Any) -> dict[str, Any]:
    return {
        "database": {"backend": "postgres"},
        "queue": {"poll_seconds": 20, "stale_lease_seconds": 600, "max_attempts": 3, **queue},
    }


def _worker(pool: FakeQueuePool, config: dict[str, Any] | None = None) -> QueueWorker:
    return QueueWorker(
        pool=pool, tenant_id=TENANT, config=config or _config(), identity=IDENT, beat_interval=0.02
    )


def _patch_ingest(monkeypatch: pytest.MonkeyPatch, fake: Any) -> None:
    monkeypatch.setattr("corpus_kb.queue.worker.ingest_book", fake)


def _success() -> dict[str, Any]:
    return {"status": "success", "run_id": RUN_ID, "chunk_count": 12}


# ---------------------------------------------------------------------------
# Claim order and the done shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_runs_books_in_queued_order(monkeypatch: pytest.MonkeyPatch) -> None:
    first = queue_row(id=QUEUE_ID, queued_at=EPOCH, content_hash="hash-a")
    second = queue_row(id=SECOND_ID, queued_at=EPOCH + timedelta(seconds=1), content_hash="hash-b")
    third = queue_row(id="00000000-0000-0000-0000-0000000000e3", queued_at=EPOCH, content_hash="h3")
    pool = FakeQueuePool([second, first, third])  # table order is not claim order
    seen: list[str] = []

    async def fake(pool: Any, config: Any, tenant_id: str, path: str) -> dict[str, Any]:
        seen.append(path)
        return _success()

    _patch_ingest(monkeypatch, fake)
    report = await _worker(pool).drain_once()
    # Same queued_at ties break by id: e1 (first), e3 (third), e2 (second).
    assert pool.claims == [first["id"], third["id"], second["id"]]
    assert report.counts() == {"done": 3, "failed": 0, "pending": 0}
    assert [o.queue_id for o in report.sorted_outcomes()] == [
        first["id"],
        third["id"],
        second["id"],
    ]


@pytest.mark.asyncio
async def test_success_fills_run_id_chunks_and_clears_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = FakeQueuePool([queue_row()])

    async def fake(pool: Any, config: Any, tenant_id: str, path: str) -> dict[str, Any]:
        return _success()

    _patch_ingest(monkeypatch, fake)
    report = await _worker(pool).drain_once()
    row = pool.rows[0]
    assert row["status"] == "done"
    assert row["run_id"] == RUN_ID
    assert row["chunks"] == 12
    assert row["pages_done"] is None and row["pages_total"] is None
    assert row["claimed_by"] is None and row["finished_at"] is not None
    outcome = report.outcomes[0]
    assert outcome.status == "done" and outcome.run_id == RUN_ID


@pytest.mark.asyncio
async def test_pg_chunk_count_wins_over_chunk_count(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = FakeQueuePool([queue_row()])

    async def fake(pool: Any, config: Any, tenant_id: str, path: str) -> dict[str, Any]:
        return {"status": "success", "pg_chunk_count": 7, "chunk_count": 99}

    _patch_ingest(monkeypatch, fake)
    await _worker(pool).drain_once()
    assert pool.rows[0]["chunks"] == 7


# ---------------------------------------------------------------------------
# The attempt ladder (one attempt per pass; retries land in later passes)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failure_counts_one_attempt_and_a_later_pass_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = FakeQueuePool([queue_row()])
    runs = {"n": 0}

    async def fake(pool: Any, config: Any, tenant_id: str, path: str) -> dict[str, Any]:
        runs["n"] += 1
        if runs["n"] == 1:
            raise RuntimeError("boom")
        return _success()

    _patch_ingest(monkeypatch, fake)
    worker = _worker(pool)
    first = await worker.drain_once()
    row = pool.rows[0]
    assert runs["n"] == 1  # the pass watermark: no same-pass retry loop
    assert row["status"] == "pending" and row["attempts"] == 1
    assert row["last_error"] == "boom"
    assert first.outcomes[0].status == "pending"
    await asyncio.sleep(0.02)  # the next pass must carry a later watermark
    second = await worker.drain_once()
    assert runs["n"] == 2 and row["status"] == "done"
    assert second.outcomes[0].status == "done" and second.outcomes[0].run_id == RUN_ID


@pytest.mark.asyncio
async def test_failure_lands_on_failed_when_retries_are_spent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = FakeQueuePool([queue_row()])

    async def fake(pool: Any, config: Any, tenant_id: str, path: str) -> dict[str, Any]:
        raise RuntimeError("boom")

    _patch_ingest(monkeypatch, fake)
    worker = _worker(pool)
    for _ in range(3):
        await asyncio.sleep(0.02)  # each pass needs a later watermark
        report = await worker.drain_once()
    row = pool.rows[0]
    assert row["status"] == "failed" and row["attempts"] == 3
    assert row["last_error"] == "boom" and row["finished_at"] is not None
    assert report.counts() == {"done": 0, "failed": 1, "pending": 0}
    assert report.outcomes[0].detail == "the retries are spent"


@pytest.mark.asyncio
async def test_pipeline_error_dict_counts_as_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = FakeQueuePool([queue_row()])

    async def fake(pool: Any, config: Any, tenant_id: str, path: str) -> dict[str, Any]:
        return {"status": "error", "message": "File not found: /tmp/book.pdf"}

    _patch_ingest(monkeypatch, fake)
    await _worker(pool).drain_once()
    row = pool.rows[0]
    assert row["status"] == "pending" and row["attempts"] == 1
    assert row["last_error"] == "File not found: /tmp/book.pdf"


@pytest.mark.asyncio
async def test_partial_run_completes_with_stage_errors_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = FakeQueuePool([queue_row()])

    async def fake(pool: Any, config: Any, tenant_id: str, path: str) -> dict[str, Any]:
        return {"status": "partial", "errors": ["EmbeddingError: offline"]}

    _patch_ingest(monkeypatch, fake)
    report = await _worker(pool).drain_once()
    row = pool.rows[0]
    assert row["status"] == "done"
    assert row["last_error"] == "EmbeddingError: offline"
    assert report.outcomes[0].status == "done"


# ---------------------------------------------------------------------------
# Stale lease and release
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_reclaim_returns_row_without_an_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale = queue_row(
        status="running", claimed_by="other:1:x", heartbeat_at=EPOCH - timedelta(seconds=700)
    )
    pool = FakeQueuePool([stale])

    async def fake(pool: Any, config: Any, tenant_id: str, path: str) -> dict[str, Any]:
        return _success()

    _patch_ingest(monkeypatch, fake)
    report = await _worker(pool).drain_once()
    row = pool.rows[0]
    assert row["attempts"] == 0  # a stale lease is a crash, not a failure
    assert row["status"] == "done"  # reclaimed, then claimed and run to done
    assert any(o.detail == "reclaimed: the worker lease ran out" for o in report.outcomes)


@pytest.mark.asyncio
async def test_fresh_lease_is_not_reclaimed(monkeypatch: pytest.MonkeyPatch) -> None:
    live = queue_row(status="running", claimed_by="other:1:x", heartbeat_at=EPOCH)
    pool = FakeQueuePool([live])

    async def fake(pool: Any, config: Any, tenant_id: str, path: str) -> dict[str, Any]:
        raise AssertionError("a running row of another worker must not be claimed")

    _patch_ingest(monkeypatch, fake)
    report = await _worker(pool).drain_once()
    assert report.outcomes == []
    assert pool.rows[0]["claimed_by"] == "other:1:x"


class _StopOnClaimPool(FakeQueuePool):
    """Sets a hook inside the claim window (the release path)."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        super().__init__(rows)
        self.on_claim: Any = None

    def _claim(self, claimed_by: object, claimed_before: Any = None) -> dict[str, Any] | None:
        if self.on_claim is not None:
            self.on_claim()
        return super()._claim(claimed_by, claimed_before)


@pytest.mark.asyncio
async def test_stop_in_the_claim_window_releases_the_row(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _StopOnClaimPool([queue_row()])
    worker = _worker(pool)
    pool.on_claim = worker.request_stop

    async def fake(pool: Any, config: Any, tenant_id: str, path: str) -> dict[str, Any]:
        raise AssertionError("the pipeline must not run for a released claim")

    _patch_ingest(monkeypatch, fake)
    report = await worker.drain_once()
    assert report.stopped and report.stop_reason == "stop requested"
    assert report.outcomes[0].detail == "released: stop landed before the run started"
    row = pool.rows[0]
    assert row["status"] == "pending" and row["claimed_by"] is None
    assert row["attempts"] == 0


# ---------------------------------------------------------------------------
# Stop during a book, empty queue, lost lease
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_during_a_book_lets_it_finish(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = FakeQueuePool([queue_row(), queue_row(id=SECOND_ID, content_hash="hash-b")])
    holder: dict[str, QueueWorker] = {}

    async def fake(pool: Any, config: Any, tenant_id: str, path: str) -> dict[str, Any]:
        holder["worker"].request_stop("signal 2")
        return _success()

    _patch_ingest(monkeypatch, fake)
    worker = _worker(pool)
    holder["worker"] = worker
    report = await worker.drain_once()
    assert report.stopped
    assert [o.status for o in report.outcomes] == ["done"]
    # The second row stays pending: no new claim after the stop.
    assert pool.rows[1]["status"] == "pending"


@pytest.mark.asyncio
async def test_drain_on_an_empty_queue_answers_zero_rows() -> None:
    report = await _worker(FakeQueuePool()).drain_once()
    assert report.outcomes == []
    assert report.counts() == {"done": 0, "failed": 0, "pending": 0}
    assert report.stopped is False


@pytest.mark.asyncio
async def test_a_lost_lease_reports_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    class _VanishingPool(FakeQueuePool):
        def _heartbeat(self, queue_id: str, claimed_by: str) -> dict[str, Any] | None:
            # The row was cancelled the moment the book started.
            self.rows[0]["status"] = "cancelled"
            return None

    pool = _VanishingPool([queue_row()])

    async def fake(pool: Any, config: Any, tenant_id: str, path: str) -> dict[str, Any]:
        await asyncio.sleep(0.05)  # let one heartbeat beat first
        return _success()

    _patch_ingest(monkeypatch, fake)
    report = await _worker(pool).drain_once()
    assert report.outcomes[0].status == "pending"
    assert report.outcomes[0].detail == "the queue lease was lost during the run"


# ---------------------------------------------------------------------------
# Watch mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watch_drains_one_pass_then_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = FakeQueuePool([queue_row()])
    holder: dict[str, QueueWorker] = {}
    calls = {"n": 0}

    async def fake(pool: Any, config: Any, tenant_id: str, path: str) -> dict[str, Any]:
        calls["n"] += 1
        holder["worker"].request_stop("one pass is enough")
        return _success()

    _patch_ingest(monkeypatch, fake)
    worker = _worker(pool, _config(poll_seconds=0.01))
    holder["worker"] = worker
    report = await worker.watch()
    assert calls["n"] == 1  # the first pass ran; the stop ended the polling
    assert report.counts() == {"done": 1, "failed": 0, "pending": 0}
    assert report.stopped


# ---------------------------------------------------------------------------
# Backend guard and the signal path
# ---------------------------------------------------------------------------


def test_duckdb_backend_is_refused_typed() -> None:
    with pytest.raises(ConfigError) as excinfo:
        QueueWorker(
            pool=FakeQueuePool(),
            tenant_id=TENANT,
            config={"database": {"backend": "duckdb"}},
        )
    assert "Postgres" in str(excinfo.value)


def test_unknown_backend_is_refused_typed() -> None:
    with pytest.raises(ConfigError):
        QueueWorker(
            pool=FakeQueuePool(),
            tenant_id=TENANT,
            config={"database": {"backend": "oracle"}},
        )


def test_stop_flag_keeps_the_first_reason() -> None:
    flag = StopFlag()
    flag.request("first")
    flag.request("second")
    assert flag.requested and flag.reason == "first"


def test_install_stop_signals_maps_sigint_onto_the_flag() -> None:
    flag = StopFlag()
    install_stop_signals(flag)
    handler = signal.getsignal(signal.SIGINT)
    assert callable(handler)
    handler(signal.SIGINT, None)
    assert flag.requested and flag.reason == f"signal {signal.SIGINT}"
    # A second SIGINT restores the default handler: Ctrl-C can force the end.
    with pytest.raises(KeyboardInterrupt):
        handler(signal.SIGINT, None)
    # Leave the suite with the default handler (no cross-test bleed).
    signal.signal(signal.SIGINT, signal.default_int_handler)
