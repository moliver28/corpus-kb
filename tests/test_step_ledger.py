"""Step ledger round-trip (TD-13): DuckDB offline, Postgres when reachable."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest

from corpus_kb._setup.migrate import run_migrations
from corpus_kb.research.step_ledger import (
    digest_ids,
    fail_step,
    finish_step,
    program_version,
    record_step,
    start_step,
    step_key,
)
from corpus_kb.research.step_ops import run_step
from corpus_kb.storage.duckdb_gateway import DuckDbGateway
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string

TENANT = UUID("00000000-0000-0000-0000-000000000001")


async def _gateway(tmp_path: Path) -> DuckDbGateway:
    path = tmp_path / f"ledger-{uuid4().hex}.duckdb"
    await run_migrations(duckdb_path_to_conn_string(path))
    return await DuckDbGateway.connect(str(path))


async def _rows(gw: DuckDbGateway) -> list[dict[str, object]]:
    return [
        dict(r)
        for r in await gw.fetch(
            "SELECT * FROM analysis_steps WHERE tenant_id=$1 ORDER BY started_at",
            str(TENANT),
        )
    ]


async def test_ledger_round_trip_on_duckdb(tmp_path: Path) -> None:
    gw = await _gateway(tmp_path)
    step_id = await start_step(
        gw,
        TENANT,
        "coding_pool",
        parameters={"codebook_version_id": "v-1"},
        used=["chunk-1", "chunk-2"],
        actor="harness",
    )
    await finish_step(gw, TENANT, step_id, outputs_table="chunk_signals", outputs_rows=7)
    planned = await start_step(gw, TENANT, "protocol_step", kind="planned", seq=1)
    await fail_step(gw, TENANT, planned)

    rows = await _rows(gw)
    assert len(rows) == 2
    completed, failed = rows[0], rows[1]
    assert completed["kind"] == "ad_hoc"
    assert completed["status"] == "completed"
    assert completed["outputs_table"] == "chunk_signals"
    assert completed["outputs_rows"] == 7
    assert completed["actor"] == "harness"
    assert completed["finished_at"] is not None
    assert failed["kind"] == "planned"
    assert failed["status"] == "failed"
    # PROV names + digests, no corpus text anywhere in the row.
    assert completed["was_derived_from"] is None
    key = str(completed["step_key"])
    assert len(key) == 64
    expected = step_key("", {"codebook_version_id": "v-1"}, None, None)
    assert key == expected
    await gw.close()


async def test_record_step_is_fail_open(tmp_path: Path) -> None:
    class Boom:
        async def fetch(self, *_a: object, **_k: object) -> list[object]:
            raise RuntimeError("no ledger table")

        async def fetchval(self, *_a: object, **_k: object) -> object:
            raise RuntimeError("no ledger table")

        async def execute(self, *_a: object, **_k: object) -> str:
            raise RuntimeError("no ledger table")

    from corpus_kb.coding._scope import CodingStore

    store = cast(CodingStore, Boom())
    assert await record_step(store, TENANT, "coding_pool") is None


async def test_record_step_refuses_planned_kind(tmp_path: Path) -> None:
    gw = await _gateway(tmp_path)
    with pytest.raises(ValueError, match="planned"):
        await record_step(gw, TENANT, "coding_pool", kind="planned")
    assert await _rows(gw) == []  # the refusal happens before any write
    await gw.close()


async def test_run_step_completes_only_after_op_returns(tmp_path: Path) -> None:
    gw = await _gateway(tmp_path)
    seen: list[str] = []

    async def op() -> int:
        rows = await _rows(gw)
        seen.append(str(rows[0]["status"]) if rows else "none")
        return 7

    result = await run_step(gw, TENANT, "coding_pool", op, parameters={"k": "v"})
    assert result == 7
    assert seen == ["running"]  # the row is still running while the op executes
    rows = await _rows(gw)
    assert rows[0]["status"] == "completed"
    assert rows[0]["operation"] == "coding_pool"
    assert rows[0]["finished_at"] is not None
    await gw.close()


async def test_run_step_records_failed_and_reraises(tmp_path: Path) -> None:
    gw = await _gateway(tmp_path)

    async def op() -> int:
        raise RuntimeError("op exploded")

    with pytest.raises(RuntimeError, match="op exploded"):
        await run_step(gw, TENANT, "coding_pool", op)
    rows = await _rows(gw)
    assert rows[0]["status"] == "failed"
    assert rows[0]["finished_at"] is not None
    await gw.close()


async def test_run_step_is_fail_open_without_ledger() -> None:
    class Boom:
        async def fetch(self, *_a: object, **_k: object) -> list[object]:
            raise RuntimeError("no ledger table")

        async def fetchval(self, *_a: object, **_k: object) -> object:
            raise RuntimeError("no ledger table")

        async def execute(self, *_a: object, **_k: object) -> str:
            raise RuntimeError("no ledger table")

    from corpus_kb.coding._scope import CodingStore

    async def op() -> str:
        return "done"

    store = cast(CodingStore, Boom())
    assert await run_step(store, TENANT, "coding_pool", op) == "done"


def test_step_key_changes_with_inputs_and_program() -> None:
    base = step_key("digest-a", {"k": 1}, "1.0.0", "model-digest")
    assert base == step_key("digest-a", {"k": 1}, "1.0.0", "model-digest")
    assert base != step_key("digest-b", {"k": 1}, "1.0.0", "model-digest")
    assert base != step_key("digest-a", {"k": 2}, "1.0.0", "model-digest")
    assert base != step_key("digest-a", {"k": 1}, "1.1.0", "model-digest")
    assert isinstance(program_version(), str)


def test_digest_ids_is_order_insensitive() -> None:
    assert digest_ids(["b", "a", "b"]) == digest_ids(["a", "b"])


@pytest.mark.asyncio
async def test_ledger_round_trip_on_postgres(pg_pool: object) -> None:
    """Same round trip against live Postgres; skips when PG is unreachable.

    The shared test database may not carry migration 050 yet, so the test
    applies the idempotent additive file first (the same bytes the runner
    applies on a fresh store).
    """
    from importlib import resources

    from corpus_kb.coding._scope import CodingStore

    store = cast(CodingStore, pg_pool)
    migration = resources.files("corpus_kb") / "migrations" / "050_analysis_steps.sql"
    await store.execute(migration.read_text(encoding="utf-8"))
    step_id = await start_step(store, TENANT, "coding_export", parameters={"limit": 5})
    await finish_step(store, TENANT, step_id, outputs_table="rows", outputs_rows=5)
    rows = await store.fetch(
        "SELECT status, outputs_rows, kind FROM analysis_steps WHERE step_id=$1 AND tenant_id=$2",
        str(step_id),
        str(TENANT),
    )
    assert rows[0]["status"] == "completed"
    assert rows[0]["outputs_rows"] == 5
    assert rows[0]["kind"] == "ad_hoc"
