"""Round-trip driver for migrate-data (issue #127, program §3.3/§4).

Both directions in one place (zero-drift rule 1):

- ``postgres_to_duckdb`` — the ATTACH leg (``migrate_attach``): run the
  DuckDB mirror migrations on the target file, ATTACH the postgres source
  READ_ONLY, INSERT ... SELECT in FK order, seed the target's SQLite
  event store, verify counts, verify the composed (corpus + events)
  digest.
- ``duckdb_to_postgres`` — the stream leg (``migrate_stream``, imported
  lazily below so the edge stays one-way): psycopg streams DuckDB rows
  into the mirrored postgres schema; the postgres event store receives
  the SQLite events through the recorder API; same verification.

Any mismatch raises ConflictError with the bank string — the digest check
runs before the report returns, so a partial copy never reads as success.
Shared helpers here serve both legs.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import replace

from corpus_kb.errors import ConflictError, ValidationError
from corpus_kb.handlers import migrate_events as events_leg
from corpus_kb.handlers.migrate_attach import (
    MIGRATE_ALIAS,
    attach_copy,
    attach_source,
    audit_numeric_columns,
    detach_source,
    drop_superseded_hnsw,
    ensure_target_empty,
    fk_insert_order,
    target_pks,
    transforms_from_target,
)
from corpus_kb.handlers.migrate_data import (
    MIGRATION_MISMATCH,
    StreamConn,
    TablePlan,
    _copy_table,
    _verify_counts,
    build_plan,
    discover_tables,
)
from corpus_kb.handlers.migrate_digest import compose_digest, corpus_digest, event_digest

DIRECTIONS = ("postgres_to_duckdb", "duckdb_to_postgres")

TYPE_NOTE_NUMERIC = (
    "Postgres numeric columns copy as DOUBLE. This is the documented precision policy."
)

#: String bank §11.15-adjacent: the RLS gate (migrations FORCE row level
#: security on every tenanted table, so a plain role reads zero rows —
#: a silent empty copy). The migration role must bypass RLS.
RLS_REQUIRED = (
    "The postgres role cannot read every row. Use a BYPASSRLS role, then run migrate-data again."
)


def assert_bypassrls(pg_dsn: str) -> None:
    """Refuse to migrate through a role row level security filters.

    FORCE RLS (migration 011) removes the owner exemption, so an
    attachment or stream session without BYPASSRLS copies empty tables
    and still matches its own counts — the one silent-loss shape this
    tool can close by check.
    """
    import psycopg

    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT coalesce(rolsuper, false) OR coalesce(rolbypassrls, false)"
            " FROM pg_roles WHERE rolname = current_user"
        )
        row = cur.fetchone()
    if not row or not row[0]:
        raise ValidationError(RLS_REQUIRED, remediation=RLS_REQUIRED)


def duckdb_swap(source_conn: StreamConn, target_conn: StreamConn, direction: str) -> dict:
    """The hermetic duckdb-to-duckdb leg (migrate_data.migrate's body).

    Same report keys and behavior the original skeleton pinned: plan,
    stream copy, count verify, then the two digests (the vector-text side
    reads through the plan transforms so both hash the FLOAT[n] form).
    """
    if direction not in DIRECTIONS:
        allowed = ", ".join(DIRECTIONS)
        raise ValueError(f"Unknown migration direction {direction!r}. Use one of: {allowed}.")
    started = time.monotonic()
    plan = build_plan(discover_tables(source_conn))
    copies = [entry for entry in plan if entry.disposition == "copy"]
    tables: list[dict[str, object]] = []
    total = 0
    for entry in copies:
        rows = _copy_table(source_conn, target_conn, entry)
        tables.append({"name": entry.name, "rows": rows})
        total += rows
    _verify_counts(source_conn, target_conn, copies)
    source_is_text = direction == "postgres_to_duckdb"
    source_digest = corpus_digest(source_conn, copies, transform_text_vectors=source_is_text)
    target_digest = corpus_digest(target_conn, copies, transform_text_vectors=not source_is_text)
    if source_digest != target_digest:
        raise ConflictError(MIGRATION_MISMATCH, remediation=MIGRATION_MISMATCH)
    return {
        "direction": direction,
        "tables": tables,
        "rows": total,
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "digest": source_digest,
        "skipped": sorted(e.name for e in plan if e.disposition != "copy"),
    }


async def run_migration(direction: str, source_dsn: str, target_dsn: str) -> dict:
    """Run one migration leg; async (the postgres target may need a schema)."""
    if direction not in DIRECTIONS:
        allowed = ", ".join(DIRECTIONS)
        raise ValueError(f"Unknown migration direction {direction!r}. Use one of: {allowed}.")
    if direction == "postgres_to_duckdb":
        return _to_duckdb(source_dsn, target_dsn)
    from corpus_kb.handlers.migrate_stream import to_postgres

    return await to_postgres(source_dsn, target_dsn)


def _mismatch() -> ConflictError:
    return ConflictError(MIGRATION_MISMATCH, remediation=MIGRATION_MISMATCH)


def _verify_event_digest(
    source: Sequence[events_leg.EventRecord],
    source_snaps: Sequence[events_leg.EventRecord],
    target: Sequence[events_leg.EventRecord],
    target_snaps: Sequence[events_leg.EventRecord],
) -> str:
    """One event digest, proven equal across both stores."""
    source_digest = event_digest(source, source_snaps)
    if source_digest != event_digest(target, target_snaps):
        raise _mismatch()
    return source_digest


def _entry_pair(
    con: StreamConn, entry: TablePlan, target_ref: str, pk_ref: str, json_ref: str
) -> tuple[TablePlan, TablePlan]:
    """Source and target plan entries sharing pk and transform sets.

    pk comes from pk_ref (the DuckDB side of the pair — the mirror holds
    the constraint DDL); JSON transforms come from json_ref, also DuckDB
    (the postgres scanner reports jsonb as VARCHAR, so the JSON set is
    unreadable from that side).
    """
    pk = target_pks(con, pk_ref)
    extended = transforms_from_target(con, json_ref, entry.transforms)
    return (
        replace(entry, pk=pk, transforms=extended),
        replace(entry, name=target_ref, pk=pk, transforms=extended),
    )


def _verify_pair_counts(
    source_conn: StreamConn, target_conn: StreamConn, pairs: Sequence[tuple[TablePlan, TablePlan]]
) -> None:
    """Per-table count check across the two legs' connections (the same
    connection on the ATTACH leg, duckdb+psycopg on the stream leg)."""
    for source_entry, target_entry in pairs:
        source = source_conn.execute(f"SELECT count(*) FROM {source_entry.name}").fetchone()
        target = target_conn.execute(f"SELECT count(*) FROM {target_entry.name}").fetchone()
        if source != target:
            raise _mismatch()


def _to_duckdb(pg_dsn: str, duckdb_dsn: str) -> dict:
    """One postgres_to_duckdb leg: ATTACH copy + events + verified digest."""
    started = time.monotonic()
    import duckdb as duckdb_module

    from corpus_kb._setup.migrate import duckdb_dsn_to_path, run_duckdb_migrations

    assert_bypassrls(pg_dsn)
    run_duckdb_migrations(duckdb_dsn)  # a fresh file gets the mirror DDL
    con = duckdb_module.connect(duckdb_dsn_to_path(duckdb_dsn))
    try:
        dropped = drop_superseded_hnsw(con)
        attach_source(con, pg_dsn)
        try:
            plan = build_plan(discover_tables(con, catalog=MIGRATE_ALIAS))
            copies = [entry for entry in plan if entry.disposition == "copy"]
            refs = [f"main.{entry.name.split('.')[-1]}" for entry in copies]
            ensure_target_empty(con, refs)
            order = fk_insert_order(con, refs)
            pairs = [
                _entry_pair(con, entry, ref, ref, ref)
                for entry, ref in zip(copies, refs, strict=True)
            ]
            tables = attach_copy(con, copies, order)
            _verify_pair_counts(con, con, pairs)
            pg_events, pg_snaps = events_leg.collect_postgres_events(pg_dsn)
            sqlite_path = events_leg.sqlite_path_for_duckdb_dsn(duckdb_dsn)
            events_leg.write_sqlite_events(sqlite_path, pg_events, pg_snaps)
            sl_events, sl_snaps = events_leg.collect_sqlite_events(sqlite_path)
            events_digest = _verify_event_digest(pg_events, pg_snaps, sl_events, sl_snaps)
            source_digest = corpus_digest(
                con, [src for src, _ in pairs], transform_text_vectors=True
            )
            target_digest = corpus_digest(
                con, [tgt for _, tgt in pairs], transform_text_vectors=False
            )
            if source_digest != target_digest:
                raise _mismatch()
            return {
                "direction": "postgres_to_duckdb",
                "tables": sorted(tables, key=lambda row: str(row["name"])),
                "rows": sum(int(row["rows"]) for row in tables),
                "elapsed_seconds": round(time.monotonic() - started, 6),
                "digest": compose_digest(source_digest, events_digest),
                "skipped": sorted(e.name for e in plan if e.disposition != "copy"),
                "events": {
                    "events": len(pg_events),
                    "snapshots": len(pg_snaps),
                    "digest": events_digest,
                },
                "type_notes": [TYPE_NOTE_NUMERIC],
                "numeric_columns": audit_numeric_columns(con, copies),
                "dropped_indexes": dropped,
            }
        finally:
            detach_source(con)
    finally:
        con.close()
