"""Event-store leg for migrate-data (issue #127, program §4).

The eventsourcing store is backend-dependent: postgres keeps the
``<app>_events``/``<app>_snapshots`` tables; the DuckDB backend keeps the
SQLite store beside the .duckdb (program doc §2 — this leg names it
``<path>.events.db``, see :func:`sqlite_path_for_duckdb_dsn`). A
corpus-table copy without this leg silently drops command history
(W0 S4). Events move through the library's recorder API on postgres
(insert_events owns the composite type); the SQLite side reads and
writes through sqlite3 with the library's fixed table shapes. Snapshots
have no bulk API on either store, so the leg issues plain inserts
against the two fixed shapes.

Records normalize to (originator hex, version, topic, state bytes) — the
fields both stores share. The per-store notification id is a local
sequence, so copies renumber it and the digest ignores it.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from typing import TYPE_CHECKING

from corpus_kb.errors import ConflictError, InternalError
from corpus_kb.handlers.migrate_attach import TARGET_NOT_EMPTY
from corpus_kb.handlers.migrate_digest import EventRecord
from corpus_kb.handlers.migrate_pg import PgCursor

if TYPE_CHECKING:
    from eventsourcing.application import Application

#: Notification page size for the postgres recorder read loop.
EVENT_PAGE = 500


def originator_hex(value: object) -> str:
    """One lowercase no-dash hex form for a UUID or its text."""
    text = str(value)
    try:
        return uuid.UUID(text).hex
    except ValueError:
        return text.replace("-", "").lower()


def _table_guard(table: str) -> str:
    if not re.fullmatch(r"[a-z_0-9]+", table):
        raise InternalError(f"Derived event-store table name {table!r} is not a bare identifier.")
    return table


def sqlite_path_for_duckdb_dsn(dsn: str) -> str:
    """The event-store SQLite path for a duckdb:/// DSN.

    Program doc §2: the store is the file BESIDE the .duckdb — the duckdb
    path plus an ``.events.db`` suffix. The shared helper in
    ``storage.duckdb_support`` owns the name (the runtime app derives
    SQLITE_DBNAME through it too), so the migrate leg and the runtime can
    never disagree again.
    """
    from corpus_kb.storage.duckdb_support import events_sidecar_path

    return events_sidecar_path(dsn)


# ---------------------------------------------------------------------------
# SQLite side (the DuckDB backend's event store)
# ---------------------------------------------------------------------------


def _sqlite_tables(path: str) -> set[str]:
    with sqlite3.connect(path) as con:
        rows = con.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {str(row[0]) for row in rows}


def collect_sqlite_events(path: str) -> tuple[list[EventRecord], list[EventRecord]]:
    """Events and snapshots from the library's fixed SQLite shapes."""
    events: list[EventRecord] = []
    snapshots: list[EventRecord] = []
    tables = _sqlite_tables(path)
    with sqlite3.connect(path) as con:
        if "stored_events" in tables:
            for row in con.execute(
                "SELECT originator_id, originator_version, topic, state FROM stored_events"
            ):
                events.append((originator_hex(row[0]), int(row[1]), str(row[2]), bytes(row[3])))
        if "stored_snapshots" in tables:
            for row in con.execute(
                "SELECT originator_id, originator_version, topic, state FROM stored_snapshots"
            ):
                snapshots.append((originator_hex(row[0]), int(row[1]), str(row[2]), bytes(row[3])))
    return events, snapshots


def write_sqlite_events(path: str, events: list[EventRecord], snapshots: list[EventRecord]) -> None:
    """Create the library's tables when missing, then copy the records.

    The library owns the DDL (``create_table``), the leg owns no schema:
    the snapshots table is the events recorder rebuilt over the
    ``stored_snapshots`` name (the eventsourcing-sqlite factory's own
    shape — there is no separate snapshot recorder class). INSERT OR
    REPLACE keeps a re-run of the leg idempotent.
    """
    from eventsourcing.sqlite import (
        SQLiteAggregateRecorder,
        SQLiteApplicationRecorder,
        SQLiteDatastore,
    )

    store = SQLiteDatastore(path)
    SQLiteApplicationRecorder(store).create_table()
    if snapshots:
        SQLiteAggregateRecorder(store, "stored_snapshots").create_table()
    with sqlite3.connect(path) as con:
        con.executemany(
            "INSERT OR REPLACE INTO stored_events"
            " (originator_id, originator_version, topic, state) VALUES (?, ?, ?, ?)",
            [(originator, version, topic, state) for originator, version, topic, state in events],
        )
        if snapshots:
            con.executemany(
                "INSERT OR REPLACE INTO stored_snapshots"
                " (originator_id, originator_version, topic, state) VALUES (?, ?, ?, ?)",
                [
                    (originator, version, topic, state)
                    for originator, version, topic, state in snapshots
                ],
            )


# ---------------------------------------------------------------------------
# Postgres side (the postgres backend's event store)
# ---------------------------------------------------------------------------


def _snapshot_table(app: Application) -> str:
    """The snapshot table for an application: lib attribute, else derived.

    eventsourcing names both stores after the application class, so the
    events name with the ``_events`` suffix swapped derives the snapshot
    name (W0 S2: corpusapplication_* on postgres, stored_* on sqlite).
    """
    from corpus_kb.domain.application import events_table_name

    events_name = events_table_name(app)
    derived = events_name.removesuffix("_events") + "_snapshots"
    store = getattr(getattr(app, "snapshots", None), "recorder", None)
    named = getattr(store, "snapshot_table_name", None)
    return _table_guard(str(named)) if named else _table_guard(derived)


def collect_postgres_events(conn_str: str) -> tuple[list[EventRecord], list[EventRecord]]:
    """Events through the recorder's notification read, snapshots via SQL."""

    from corpus_kb.domain.application import CorpusApplication

    app = CorpusApplication(conn_str, backend="postgres")
    events: list[EventRecord] = []
    start: int | None = None
    while True:
        batch = app.recorder.select_notifications(start=start, limit=EVENT_PAGE)
        if not batch:
            break
        for note in batch:
            events.append(
                (
                    originator_hex(note.originator_id),
                    int(note.originator_version),
                    str(note.topic),
                    bytes(note.state),
                )
            )
        if len(batch) < EVENT_PAGE:
            break
        start = int(batch[-1].id) + 1
    snapshots = _collect_postgres_snapshots(conn_str, _snapshot_table(app))
    return events, snapshots


def _collect_postgres_snapshots(conn_str: str, table: str) -> list[EventRecord]:
    import psycopg

    snapshots: list[EventRecord] = []
    with psycopg.connect(conn_str) as conn, PgCursor(conn.cursor()) as cur:
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_name = %s)",
            (table,),
        )
        present = cur.fetchone()
        if not present or not present[0]:
            return snapshots
        cur.execute(
            f"SELECT originator_id, originator_version, topic, state FROM {table}"
            " ORDER BY 1, 2"  # identifiers guarded by _table_guard
        )
        for originator, version, topic, state in cur.fetchall():
            snapshots.append((originator_hex(originator), int(version), str(topic), bytes(state)))
    return snapshots


def assert_postgres_events_empty(conn_str: str) -> None:
    """The re-run gate for the PG event leg: both event tables must be empty.

    The corpus-table empty gate never sees the eventsourcing tables —
    they live beside it. ``insert_events`` has no upsert (the recorder
    owns the composite type), so a re-run against a populated store would
    crash half-written; the leg refuses first, with the same message the
    corpus gate raises.
    """
    import psycopg

    from corpus_kb.domain.application import CorpusApplication, events_table_name

    app = CorpusApplication(conn_str, backend="postgres")
    with psycopg.connect(conn_str) as conn, PgCursor(conn.cursor()) as cur:
        for table in (_snapshot_table(app), events_table_name(app)):
            statements = {"count": f"SELECT count(*) FROM {table}"}
            cur.execute(statements["count"])
            row = cur.fetchone()
            if row and row[0]:
                raise ConflictError(TARGET_NOT_EMPTY, remediation=TARGET_NOT_EMPTY)


def write_postgres_events(
    conn_str: str, events: list[EventRecord], snapshots: list[EventRecord]
) -> None:
    """Events through the recorder API (it owns the PG composite type);
    snapshots through the fixed-shape upsert."""
    import psycopg
    from eventsourcing.persistence import StoredEvent

    from corpus_kb.domain.application import CorpusApplication

    app = CorpusApplication(conn_str, backend="postgres")
    stored = [
        StoredEvent(
            originator_id=uuid.UUID(originator),
            originator_version=version,
            topic=topic,
            state=state,
        )
        for originator, version, topic, state in events
    ]
    app.recorder.insert_events(stored)
    if not snapshots:
        return
    table = _snapshot_table(app)
    with psycopg.connect(conn_str) as conn, PgCursor(conn.cursor()) as cur:
        cur.executemany(
            f"INSERT INTO {table} (originator_id, originator_version, topic, state)"
            " VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (originator_id, originator_version)"
            " DO UPDATE SET topic = EXCLUDED.topic, state = EXCLUDED.state",
            [
                (uuid.UUID(originator), version, topic, state)
                for originator, version, topic, state in snapshots
            ],
        )
