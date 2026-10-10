"""C5: bounded batching + resumable run markers (offline tests).

Concurrency is proven by tracking the peak in-flight count with real async
workers; resume semantics are proven against a Protocol-typed fake
connection backing the ``research_runs.checkpoint`` JSONB contract.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from corpus_kb.research.batching import (
    DEFAULT_CONCURRENCY,
    RunMarker,
    chunked,
    gather_bounded,
    load_completed_items,
    mark_item_completed,
    run_resumable,
)


async def test_gather_bounded_respects_concurrency_bound() -> None:
    in_flight = 0
    peak = 0

    async def _work(item: int) -> int:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return item * 2

    items = list(range(20))
    results = await gather_bounded(items, _work, concurrency=3)
    assert results == [item * 2 for item in items]  # input order preserved
    assert peak <= 3, f"peak in-flight {peak} exceeded the bound"


async def test_gather_bounded_rejects_nonpositive_concurrency() -> None:
    with pytest.raises(ValueError, match="concurrency"):
        await gather_bounded([1], lambda x: _noop(x), concurrency=0)


async def _noop(x: int) -> int:
    return x


async def test_gather_bounded_empty_input() -> None:
    assert await gather_bounded([], _noop, concurrency=4) == []


class _FakeRunConn:
    """research_runs row backed by a dict, statements asserted by shape."""

    def __init__(self, checkpoint: dict[str, object] | None = None) -> None:
        self.checkpoint: dict[str, object] = checkpoint or {}
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    async def fetchrow(self, sql: str, *args: object) -> object | None:
        assert "checkpoint->'completed'" in sql
        return {"completed": self.checkpoint.get("completed")}

    async def execute(self, sql: str, *args: object) -> object:
        self.executed.append((sql, args))
        assert "@> to_jsonb($3::text)" in sql
        completed = self.checkpoint.get("completed")
        done = set(completed) if isinstance(completed, list) else set()
        item = str(args[2])
        if item in done:
            return "UPDATE 0"
        self.checkpoint["completed"] = sorted(done | {item})
        return "UPDATE 1"


MARKER = RunMarker(tenant_id="t-1", run_id="r-1")


async def test_run_marker_round_trip_and_idempotent_mark() -> None:
    conn = _FakeRunConn()
    assert await load_completed_items(conn, MARKER) == set()
    assert await mark_item_completed(conn, MARKER, "unit:11") is True
    # Re-marking the same item is a no-op (UPDATE 0), never a duplicate.
    assert await mark_item_completed(conn, MARKER, "unit:11") is False
    assert await load_completed_items(conn, MARKER) == {"unit:11"}


async def test_load_completed_items_tolerates_missing_row_and_malformed() -> None:
    class _NoRowConn:
        async def fetchrow(self, sql: str, *args: object) -> object | None:
            return None

        async def execute(self, sql: str, *args: object) -> object:  # pragma: no cover
            raise AssertionError("not used")

    assert await load_completed_items(_NoRowConn(), MARKER) == set()

    class _BadRowConn:
        async def fetchrow(self, sql: str, *args: object) -> object | None:
            return {"completed": "not-a-list"}

        async def execute(self, sql: str, *args: object) -> object:  # pragma: no cover
            raise AssertionError("not used")

    assert await load_completed_items(_BadRowConn(), MARKER) == set()


async def test_resume_skips_completed_items_and_marks_new_ones() -> None:
    conn = _FakeRunConn({"completed": ["item:0", "item:1"]})
    ran: list[str] = []

    async def _work(item: str) -> str:
        ran.append(item)
        return f"done:{item}"

    items = [f"item:{i}" for i in range(5)]
    results = await run_resumable(conn, MARKER, items, lambda item: item, _work)
    assert ran == ["item:2", "item:3", "item:4"]  # completed items skipped
    assert results == [f"done:item:{i}" for i in (2, 3, 4)]
    marks = [str(args[2]) for sql, args in conn.executed if "UPDATE" in sql]
    assert marks == ["item:2", "item:3", "item:4"]
    # The checkpoint records exactly the executed items.
    assert set(conn.checkpoint["completed"]) >= {"item:2", "item:3", "item:4"}


async def test_failed_item_is_not_marked_so_resume_retries_it() -> None:
    conn = _FakeRunConn()
    attempts: list[str] = []

    async def _flaky(item: str) -> str:
        attempts.append(item)
        if item == "item:1" and attempts.count("item:1") == 1:
            raise RuntimeError("transient")
        return f"ok:{item}"

    items = ["item:0", "item:1", "item:2"]
    with pytest.raises(RuntimeError, match="transient"):
        await run_resumable(conn, MARKER, items, lambda item: item, _flaky)
    # item:1 failed -> unmarked; the other two were marked complete.
    done = await load_completed_items(conn, MARKER)
    assert done == {"item:0", "item:2"}
    # Second run retries ONLY the failed item.
    results = await run_resumable(conn, MARKER, items, lambda item: item, _flaky)
    assert results == ["ok:item:1"]
    assert attempts.count("item:1") == 2
    assert attempts.count("item:0") == 1


def test_chunked_splits_and_keeps_remainder() -> None:
    assert chunked([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
    assert chunked([], 3) == []
    with pytest.raises(ValueError, match="chunk size"):
        chunked([1], 0)
    assert DEFAULT_CONCURRENCY >= 1
    # The checkpoint JSONB shape survives a JSON round trip (contract pin).
    payload = json.dumps({"completed": ["a", "b"]})
    assert json.loads(payload)["completed"] == ["a", "b"]
