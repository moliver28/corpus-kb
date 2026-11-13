"""Event-store health read body (issue #350).

``events_summary`` is the ONE read that replaces what plugin 0.6.0 did
with four SQL queries plus a direct open of the DuckDB event-store
sidecar. The engine owns that knowledge now:

* Projection checkpoints, dead letters, and the ingest-run ledger are
  corpus tables — they read through the SAME ``SqlGateway`` seam the
  projections write through (tenant GUC inside the transaction on
  Postgres, explicit ``tenant_id`` predicate on DuckDB).
* The event store itself is lib-owned: the ``<app>_events`` table on
  Postgres (read through the same pool, exactly like
  ``projections/event_reader.py``), or the SQLite ``stored_events``
  sidecar beside the ``.duckdb`` (path owned by
  ``storage.duckdb_support.events_sidecar_path``, read with stdlib
  ``sqlite3`` in a worker thread — callers never open store files).

An absent event store answers zero counts, never a crash: a fresh
database has no events yet, and that is honest, not an error.
"""

from __future__ import annotations

import asyncio
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

import asyncpg

from corpus_kb.storage.gateway import SqlGateway, as_gateway, gateway_backend

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_DLQ_NEWEST_LIMIT = 5
_RUN_LIMIT = 5

#: The lib's fixed SQLite events table (eventsourcing.sqlite recorder).
_SQLITE_EVENTS_TABLE = "stored_events"


def _events_table() -> str:
    """The Postgres events table for our ONE application class.

    The eventsourcing lib names the table ``<application name>_events``
    with the name lowercased (``CorpusApplication`` ->
    ``corpusapplication_events``); derived here so a health read never
    constructs the app (an app construction opens store connections).
    """
    from corpus_kb.domain.application import CorpusApplication

    name = f"{type(CorpusApplication).__name__.lower()}_events"
    if not _IDENTIFIER.match(name):
        raise ValueError(f"unsafe events table name: {name!r}")
    return name


def _sql(is_duckdb: bool) -> dict[str, str]:
    """The backend statement variants for the corpus-table reads."""
    tenant = "$1" if is_duckdb else "$1::uuid"
    return {
        "checkpoints": (
            "SELECT projection_name, last_event_id, last_event_timestamp,"
            " checkpoint_timestamp, last_sequence FROM projection_checkpoints"
            f" WHERE tenant_id = {tenant} ORDER BY projection_name"
        ),
        "dlq_count": (
            f"SELECT count(*) FROM projection_dlq WHERE tenant_id = {tenant} AND resolved = FALSE"
        ),
        "dlq_newest": (
            "SELECT dlq_id, projection_name, event_id, event_type, error_message,"
            " retry_count, created_at FROM projection_dlq"
            f" WHERE tenant_id = {tenant} AND resolved = FALSE"
            f" ORDER BY created_at DESC, dlq_id LIMIT {int(_DLQ_NEWEST_LIMIT)}"
        ),
        "runs": (
            "SELECT run_id, status, started_at, finished_at, error FROM ingest_runs"
            f" WHERE tenant_id = {tenant}"
            " ORDER BY started_at DESC, run_id DESC"
            f" LIMIT {int(_RUN_LIMIT)}"
        ),
    }


def _iso(value: Any) -> str | None:
    """Timestamps leave SQL as datetimes; the envelope carries ISO text."""
    return value.isoformat() if hasattr(value, "isoformat") else value


async def _postgres_event_counts(tx: SqlGateway, table: str) -> tuple[dict[str, int], int]:
    """Topic counts + the latest notification id from the lib's PG table.

    Same pool, same house pattern as ``projections/event_reader.py``: the
    lib-owned table is read SELECT-only through the caller's gateway. An
    absent table (fresh database, app never constructed) answers zeros.
    """
    if not await tx.fetchval(f"SELECT to_regclass('public.{table}')"):
        return {}, 0
    rows = await tx.fetch(
        f"SELECT topic, count(*) AS n FROM public.{table} GROUP BY topic ORDER BY topic"
    )
    latest = await tx.fetchval(f"SELECT COALESCE(MAX(notification_id), 0) FROM public.{table}")
    return {str(row["topic"]): int(row["n"]) for row in rows}, int(latest or 0)


def _sqlite_event_counts(path: str) -> tuple[dict[str, int], int]:
    """Topic counts + the latest notification id from the SQLite sidecar.

    The lib's fixed shape: ``stored_events(topic, state, ...)`` with the
    rowid as the notification sequence (the recorder's own
    ``MAX(rowid)`` probe). Read-only, so a health read never creates the
    sidecar or contends with the lib's writer.
    """
    if not Path(path).exists():
        return {}, 0
    counts: dict[str, int] = {}
    latest = 0
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = {
            str(r[0]) for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if _SQLITE_EVENTS_TABLE in tables:
            counts = {
                str(topic): int(n)
                for topic, n in con.execute(
                    f"SELECT topic, COUNT(*) FROM {_SQLITE_EVENTS_TABLE} GROUP BY topic"
                )
            }
            row = con.execute(
                f"SELECT COALESCE(MAX(rowid), 0) FROM {_SQLITE_EVENTS_TABLE}"
            ).fetchone()
            latest = int(row[0]) if row else 0
    finally:
        con.close()
    return counts, latest


def _dsn(config: dict[str, Any] | None) -> str:
    """The connection string the event store resolves from (config, then env)."""
    dsn = str(((config or {}).get("database") or {}).get("connection_string") or "")
    return dsn or os.environ.get("CORPUS_KB_DATABASE_URL", "")


async def events_summary(
    pool: asyncpg.Pool | SqlGateway,
    tenant_id: str,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Event counts by topic, latest notification id, checkpoints, DLQ, runs.

    Returns ``{status, events_store, events_by_topic, latest_notification_id,
    checkpoints, dead_letters, recent_runs}``. ``events_store`` names the
    store kind actually read ("postgres" or "sqlite"). Checkpoints, dead
    letters, and runs are tenant-scoped; the event counts are global (the
    event store carries no tenant column).
    """
    gw = as_gateway(pool)
    is_duckdb = gateway_backend(gw) == "duckdb"
    sql = _sql(is_duckdb)
    async with gw.transaction(tenant_id) as tx:
        if is_duckdb:
            counts, latest = await asyncio.to_thread(
                _sqlite_event_counts, _sidecar_path(_dsn(config))
            )
            events_store = "sqlite"
        else:
            counts, latest = await _postgres_event_counts(tx, _events_table())
            events_store = "postgres"
        checkpoint_rows = await tx.fetch(sql["checkpoints"], tenant_id)
        dlq_count = await tx.fetchval(sql["dlq_count"], tenant_id)
        dlq_rows = await tx.fetch(sql["dlq_newest"], tenant_id)
        run_rows = await tx.fetch(sql["runs"], tenant_id)
    return {
        "status": "success",
        "events_store": events_store,
        "events_by_topic": dict(sorted(counts.items())),
        "latest_notification_id": latest,
        "checkpoints": [
            {
                "projection_name": str(row["projection_name"]),
                "last_event_id": str(row["last_event_id"]),
                "last_event_timestamp": _iso(row["last_event_timestamp"]),
                "checkpoint_timestamp": _iso(row["checkpoint_timestamp"]),
                "last_sequence": int(row["last_sequence"])
                if row["last_sequence"] is not None
                else None,
            }
            for row in checkpoint_rows
        ],
        "dead_letters": {
            "count": int(dlq_count or 0),
            "newest": [
                {
                    "dlq_id": str(row["dlq_id"]),
                    "projection_name": str(row["projection_name"]),
                    "event_id": str(row["event_id"]),
                    "event_type": str(row["event_type"]),
                    "error_message": row["error_message"],
                    "retry_count": int(row["retry_count"]),
                    "created_at": _iso(row["created_at"]),
                }
                for row in dlq_rows
            ],
        },
        "recent_runs": [
            {
                "run_id": str(row["run_id"]),
                "status": row["status"],
                "started_at": _iso(row["started_at"]),
                "finished_at": _iso(row["finished_at"]),
                "error": row["error"],
            }
            for row in run_rows
        ],
    }


def _sidecar_path(dsn: str) -> str:
    """The SQLite sidecar path for a duckdb DSN (the one owner resolves it)."""
    from corpus_kb.storage.duckdb_support import events_sidecar_path

    return events_sidecar_path(dsn)
