"""The PG baseline probe null-masks DBCV values (#527 P3).

The old ``?`` key-existence probe let a checkpoint whose
``dbcv_relative_validity`` is JSON null pass, and ``LIMIT 1`` then hid
older real values. The fixed probe masks on the extracted value, so both
engines answer the same newest-real-value semantics.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from corpus_kb.research.inductive_store import latest_baseline_dbcv

TENANT = UUID("00000000-0000-0000-0000-000000000001")
RUN = UUID("00000000-0000-0000-0000-000000000002")


class _FakeConn:
    """SqlConn double: hands back scripted rows, records the SQL."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.sql_seen = ""

    async def fetch(self, sql: str, /, *args: object) -> list[dict[str, Any]]:
        self.sql_seen = sql
        return self._rows


def _fake_scope(conn: _FakeConn) -> Any:
    @asynccontextmanager
    async def scope(store: Any, tenant_id: Any) -> Any:
        yield conn

    return scope


async def test_null_mask_excludes_unmasked_rows(monkeypatch: Any) -> None:
    """A JSON-null DBCV row cannot win and cannot mask an older real row.

    The fake conn returns what the masked probe would hand back newest-
    first: a null-valued checkpoint above a real one. The probe SQL must
    carry the value mask (not the bare ``?`` key probe), and the answer
    is the older real value.
    """
    import corpus_kb.research.inductive_store as mod

    conn = _FakeConn(
        [
            {"checkpoint": json.dumps({"dbcv_relative_validity": None})},
            {"checkpoint": json.dumps({"dbcv_relative_validity": 0.5})},
        ]
    )
    monkeypatch.setattr(mod, "tenant_scope", _fake_scope(conn))
    value = await latest_baseline_dbcv(_FakeStore(), TENANT, RUN, duck=False)
    assert "->>'dbcv_relative_validity' IS NOT NULL" in conn.sql_seen
    assert "checkpoint ? " not in conn.sql_seen
    assert value == 0.5


class _FakeStore:
    """The store argument passes through the faked tenant_scope unused."""


async def test_duck_arm_skips_null_dbcv_checkpoints(tmp_path: Any) -> None:
    """The real duck engine answers the older real value, not the null."""
    from corpus_kb._setup.migrate import run_migrations
    from corpus_kb.storage.duckdb_gateway import DuckDbGateway
    from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string

    path = tmp_path / "masking.duckdb"
    await run_migrations(duckdb_path_to_conn_string(path))
    gateway = await DuckDbGateway.connect(str(path))
    try:
        for run_id, started, checkpoint in (
            (RUN, "2026-01-01 00:00:00+00", {"dbcv_relative_validity": 0.7}),
            (
                UUID("00000000-0000-0000-0000-000000000003"),
                "2026-01-02 00:00:00+00",
                {"kind": "manifest"},
            ),
            (
                UUID("00000000-0000-0000-0000-000000000004"),
                "2026-01-03 00:00:00+00",
                {"dbcv_relative_validity": None},
            ),
        ):
            await gateway.execute(
                "INSERT INTO research_runs (run_id, tenant_id, params, state,"
                " checkpoint, started_at)"
                " VALUES ($1, $2, '{}', 'stopped', $3, $4)",
                str(run_id),
                str(TENANT),
                json.dumps(checkpoint),
                started,
            )
        assert await latest_baseline_dbcv(gateway, TENANT, UUID(int=0xA), duck=True) == 0.7
    finally:
        await gateway.close()
