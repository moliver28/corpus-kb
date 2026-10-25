"""Stream leg: DuckDB -> Postgres for migrate-data (issue #127).

The postgres extension writes row-by-row (slow), so this direction
streams through psycopg instead (program §3.3): the core stream copy
fetches DuckDB row batches and psycopg executemany inserts them into the
mirrored schema. Float lists re-format to pgvector '[v, ...]' text; the
event store moves first so a corpus-copy failure cannot strand the
command history decision. Sync psycopg calls inside the async driver:
this is the CLI-only, single-user swap.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, cast

from corpus_kb.errors import ConflictError
from corpus_kb.handlers import migrate_events as events_leg
from corpus_kb.handlers.migrate_attach import (
    SEED_TENANT_ID,
    TARGET_NOT_EMPTY,
    fk_insert_order,
)
from corpus_kb.handlers.migrate_data import (
    StreamConn,
    TablePlan,
    _columns,
    _copy_table,
    build_plan,
    discover_tables,
)
from corpus_kb.handlers.migrate_digest import compose_digest, corpus_digest
from corpus_kb.handlers.migrate_pg import PgCursor
from corpus_kb.handlers.migrate_roundtrip import (
    _entry_pair,
    _mismatch,
    _verify_event_digest,
    _verify_pair_counts,
)

if TYPE_CHECKING:
    import psycopg
    from psycopg.abc import QueryNoTemplate


class _PgStream:
    """StreamConn adapter over a sync psycopg connection.

    The core builds SQL with '?' marks; psycopg wants '%s'. Only
    leg-generated SQL passes through here (no '?' inside string
    literals), so the translation is safe.
    """

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn
        self._cur = conn.cursor()

    def execute(self, sql: str, params: Sequence[object] | None = None) -> _PgStream:
        self._cur.execute(
            cast("QueryNoTemplate", sql.replace("?", "%s")),
            params,
        )
        return self

    def executemany(self, sql: str, seq: Sequence[Sequence[object]]) -> _PgStream:
        self._cur.executemany(
            cast("QueryNoTemplate", sql.replace("?", "%s")),
            seq,
        )
        return self

    def fetchmany(self, size: int) -> Sequence[Sequence[object]]:
        return self._cur.fetchmany(size)

    def fetchall(self) -> Sequence[Sequence[object]]:
        return self._cur.fetchall()

    def fetchone(self) -> Sequence[object] | None:
        return self._cur.fetchone()


def _to_pg_vector(value: object) -> object:
    """FLOAT[n] list to pgvector '[v, ...]' text; other values pass."""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(repr(float(item)) for item in value) + "]"
    return str(value)


def _pg_row_transform(con: StreamConn, entry: TablePlan) -> Callable[[Sequence[object]], tuple]:
    """Per-column row mapping for the postgres inserts (vector columns)."""
    transforms = {t.column for t in entry.transforms if t.kind == "vector"}
    steps = tuple(
        _to_pg_vector if column in transforms else (lambda value: value)
        for column in _columns(con, entry.name)
    )

    def apply(row: Sequence[object]) -> tuple:
        return tuple(step(value) for step, value in zip(steps, row, strict=True))

    return apply


def _pg_refs(pg_dsn: str, bares: Sequence[str]) -> dict[str, str]:
    """Resolve each mirror table on the target ('public.x', else 'corpus.x')."""
    import psycopg

    refs: dict[str, str] = {}
    with psycopg.connect(pg_dsn) as conn, PgCursor(conn.cursor()) as cur:
        for bare in bares:
            for candidate in (f"public.{bare}", f"corpus.{bare}"):
                # to_regclass output omits the search_path schema — keep
                # the candidate string as the qualified ref.
                cur.execute("SELECT to_regclass(%s)", (candidate,))
                row = cur.fetchone()
                if row and row[0] is not None:
                    refs[bare] = candidate
                    break
            if bare not in refs:
                remediation = "Apply the mirror schema to the target, then run migrate-data again."
                raise ConflictError(
                    f"The target database is missing table {bare}.",
                    remediation=remediation,
                )
    return refs


async def _prepare_pg_target(pg_dsn: str, copies: Sequence[TablePlan]) -> dict[str, str]:
    """Schema when missing (shared bootstrap), then the empty-target gate."""
    import asyncpg

    from corpus_kb._setup.schema_apply import apply_schema

    conn = await asyncpg.connect(pg_dsn)
    try:
        has_mirror = await conn.fetchval("SELECT to_regclass('public.documents')")
    finally:
        await conn.close()
    if has_mirror is None:
        await apply_schema(pg_dsn)
    refs = _pg_refs(pg_dsn, [entry.name.split(".")[-1] for entry in copies])
    import psycopg

    with psycopg.connect(pg_dsn) as conn, PgCursor(conn.cursor()) as cur:
        for bare in sorted(refs):
            predicate = ""
            if bare == "tenants":
                predicate = f" WHERE tenant_id != '{SEED_TENANT_ID}'"
            cur.execute(f"SELECT count(*) FROM {refs[bare]}{predicate}")
            row = cur.fetchone()
            if row and row[0]:
                raise ConflictError(TARGET_NOT_EMPTY, remediation=TARGET_NOT_EMPTY)
    return refs


async def to_postgres(duckdb_dsn: str, pg_dsn: str) -> dict:
    """One duckdb_to_postgres leg: stream copy + events + verified digest."""
    started = time.monotonic()
    import duckdb as duckdb_module
    import psycopg

    from corpus_kb._setup.migrate import duckdb_dsn_to_path
    from corpus_kb.handlers.migrate_roundtrip import assert_bypassrls

    assert_bypassrls(pg_dsn)
    con = duckdb_module.connect(duckdb_dsn_to_path(duckdb_dsn), read_only=True)
    try:
        plan = build_plan(discover_tables(con))
        copies = [entry for entry in plan if entry.disposition == "copy"]
        refs = await _prepare_pg_target(pg_dsn, copies)
        pairs = [
            _entry_pair(con, entry, refs[entry.name.split(".")[-1]], entry.name, entry.name)
            for entry in copies
        ]
        # The corpus empty gate above never sees the eventsourcing tables;
        # the event leg refuses a populated store before its first write.
        events_leg.assert_postgres_events_empty(pg_dsn)
        sqlite_path = events_leg.sqlite_path_for_duckdb_dsn(duckdb_dsn)
        sl_events, sl_snaps = events_leg.collect_sqlite_events(sqlite_path)
        events_leg.write_postgres_events(pg_dsn, sl_events, sl_snaps)
        pg_events, pg_snaps = events_leg.collect_postgres_events(pg_dsn)
        events_digest = _verify_event_digest(sl_events, sl_snaps, pg_events, pg_snaps)
        order = fk_insert_order(con, [entry.name for entry in copies])
        by_bare = {entry.name.split(".")[-1]: entry for entry in copies}
        tables: list[dict[str, str | int]] = []
        with psycopg.connect(pg_dsn) as pgconn:
            stream = _PgStream(pgconn)
            if "tenants" in refs:  # the schema seed; the copy re-inserts it
                stream.execute(
                    f"DELETE FROM {refs['tenants']} WHERE tenant_id = '{SEED_TENANT_ID}'"
                )
            for ref in order:
                bare = ref.rsplit(".", 1)[-1]
                rows = _copy_table(
                    con,
                    stream,
                    by_bare[bare],
                    reparse=False,
                    transform_row=_pg_row_transform(con, by_bare[bare]),
                    target_name=refs[bare],
                )
                tables.append({"name": bare, "rows": rows})
            pgconn.commit()
            _verify_pair_counts(con, stream, pairs)
            source_digest = corpus_digest(
                con, [src for src, _ in pairs], transform_text_vectors=False
            )
            target_digest = corpus_digest(
                stream, [tgt for _, tgt in pairs], transform_text_vectors=True, python_parse=True
            )
        if source_digest != target_digest:
            raise _mismatch()
        return {
            "direction": "duckdb_to_postgres",
            "tables": sorted(tables, key=lambda row: str(row["name"])),
            "rows": sum(int(row["rows"]) for row in tables),
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "digest": compose_digest(source_digest, events_digest),
            "skipped": sorted(entry.name for entry in plan if entry.disposition != "copy"),
            "events": {
                "events": len(sl_events),
                "snapshots": len(sl_snaps),
                "digest": events_digest,
            },
        }
    finally:
        con.close()
