"""ATTACH leg: Postgres -> DuckDB through DuckDB's postgres extension.

The extension reads the source with pushdown (no Python row loop); the
DuckDB target keeps its DDL-mirror tables, so constraints survive the
copy (CTAS would drop them — W0 S3). ``LOAD postgres`` precedes ATTACH;
the attachment opens READ_ONLY (W0: never attach read-write in a
migration). halfvec/vector arrive as '[v, v, ...]' VARCHAR and re-parse
through the plan's column transforms; unbounded numeric arrives as
DOUBLE (the documented precision policy).

Target-side preparation lives here too: superseded runtime-managed vss
HNSW indexes are dropped (they are rebuilt, never migrated — W0: the
mirror's migrations ship none), PK columns are read for digest ordering,
and the FK graph fixes the insert order.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Sequence
from typing import cast
from urllib.parse import urlparse

from corpus_kb.contracts.model import DEFAULT_TENANT_ID as SEED_TENANT_ID
from corpus_kb.errors import ConflictError
from corpus_kb.handlers.migrate_data import (
    ColumnTransform,
    StreamConn,
    TablePlan,
    _columns,
    _select_parts,
)
from corpus_kb.storage.sql_ident import (
    quoted_identifier,
    sql_identifier,
    sql_literal,
    sql_qualified,
)

#: The read-only attachment alias for the postgres source.
MIGRATE_ALIAS = "migrate_src"

#: String bank §11.15 — the target must be empty before a swap.
TARGET_NOT_EMPTY = (
    "The target database already has data. Use an empty database, then run migrate-data again."
)

# SEED_TENANT_ID above is the schema-seeded default tenant (storage.sql and
# the DuckDB mirror both insert it). It is schema, not data: the empty gate
# ignores it and the copy clears it first, so the source's row re-seeds the
# target. It aliases the canonical contracts constant.


def libpq_from_dsn(dsn: str) -> str:
    """One libpq 'key=value' string from a postgres:// URL (W0 DSN shape)."""
    if "://" not in dsn:
        return dsn
    parsed = urlparse(dsn)
    parts = []
    if parsed.hostname:
        parts.append(f"host={_pq_quote(parsed.hostname)}")
    if parsed.port:
        parts.append(f"port={parsed.port}")
    if parsed.username:
        parts.append(f"user={_pq_quote(parsed.username)}")
    if parsed.password is not None:
        parts.append(f"password={_pq_quote(parsed.password)}")
    if parsed.path.lstrip("/"):
        parts.append(f"dbname={_pq_quote(parsed.path.lstrip('/'))}")
    return " ".join(parts)


def _pq_quote(value: str) -> str:
    if value and re.fullmatch(r"[A-Za-z0-9_]+", value):
        return value
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def attach_source(con: StreamConn, pg_dsn: str, alias: str = MIGRATE_ALIAS) -> None:
    """LOAD postgres, then ATTACH the source READ_ONLY (W0 S3 order).

    ATTACH grammar takes a literal DSN, not a bound parameter (duckdb
    discussion #14830), so the DSN is escaped here. A single quote or a
    NUL byte is refused (fail closed) — put such secrets in PGPASSFILE.
    """
    con.execute("LOAD postgres")
    if "'" in pg_dsn or "\x00" in pg_dsn:
        raise ValueError("the postgres DSN must not contain quotes or NUL bytes")
    con.execute(
        "ATTACH "
        + sql_literal(libpq_from_dsn(pg_dsn))
        + " AS "
        + sql_identifier(alias)
        + " (TYPE POSTGRES, READ_ONLY)"
    )


def detach_source(con: StreamConn, alias: str = MIGRATE_ALIAS) -> None:
    """Best-effort detach; a still-open attachment blocks file cleanup."""
    with contextlib.suppress(Exception):
        statements = {"detach": "DETACH " + sql_identifier(alias)}
        con.execute(statements["detach"])


def drop_superseded_hnsw(con: StreamConn) -> list[str]:
    """Drop runtime-managed vss HNSW indexes on the target; report names.

    The mirror's migrations ship no vss index; a target that served
    queries may carry one. After a bulk load it is superseded, and the
    exact-scan default stays correct without it. The vss-only session
    flag rides here (the SET fails while vss is not loaded, and it
    matters only when a vss object exists).
    """
    with contextlib.suppress(Exception):  # extension needed only then
        con.execute("LOAD vss")
        con.execute("SET hnsw_enable_experimental_persistence = 1")
    rows = con.execute(
        "SELECT schema_name, index_name FROM duckdb_indexes()"
        " WHERE lower(coalesce(sql, '')) LIKE '%hnsw%' OR lower(index_name) LIKE '%hnsw%'"
    ).fetchall()
    dropped = []
    for schema, index in rows:
        con.execute(
            "DROP INDEX " + quoted_identifier(str(schema)) + "." + quoted_identifier(str(index))
        )
        dropped.append(f"{schema}.{index}")
    return dropped


def target_pks(con: StreamConn, ref: str) -> tuple[str, ...]:
    """PK columns of one target table (mirror parity keeps them true on
    the source); empty when the table has no PK."""
    schema, bare = ref.rsplit(".", 1)
    rows = con.execute(
        "SELECT constraint_column_names FROM duckdb_constraints()"
        " WHERE database_name = current_database() AND schema_name = ?"
        " AND table_name = ? AND constraint_type = 'PRIMARY KEY'"
        " ORDER BY constraint_index LIMIT 1",
        [schema, bare],
    ).fetchall()
    if not rows or not isinstance(rows[0][0], (list, tuple)):
        return ()
    return tuple(str(name) for name in rows[0][0])


def json_columns(con: StreamConn, ref: str) -> tuple[str, ...]:
    """JSON-typed columns of one target table (the jsonb mirror set)."""
    schema, bare = ref.rsplit(".", 1)
    rows = con.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_catalog = current_database() AND table_schema = ?"
        " AND table_name = ? AND data_type = 'JSON' ORDER BY ordinal_position",
        [schema, bare],
    ).fetchall()
    return tuple(str(row[0]) for row in rows)


def fk_insert_order(con: StreamConn, refs: Sequence[str]) -> tuple[str, ...]:
    """Kahn order over the target's FK graph, alphabetical tiebreak.

    ``refs`` are 'schema.table' target names; the result orders the same
    refs so referenced tables insert first (sorted-name order violates
    FKs — the mirror enforces them, unlike CTAS).
    """
    deps: dict[str, set[str]] = {ref: set() for ref in refs}
    bare_of = {ref: ref.rsplit(".", 1)[-1] for ref in refs}
    for ref in refs:
        schema, bare = ref.rsplit(".", 1)
        rows = con.execute(
            "SELECT referenced_table FROM duckdb_constraints()"
            " WHERE database_name = current_database() AND schema_name = ?"
            " AND table_name = ? AND constraint_type = 'FOREIGN KEY'",
            [schema, bare],
        ).fetchall()
        for (referenced,) in rows:
            dep = str(referenced).rsplit(".", 1)[-1]
            for other in refs:
                if other != ref and bare_of[other] == dep:
                    deps[ref].add(other)
    ordered: list[str] = []
    pending = sorted(deps)
    while pending:
        done = set(ordered)
        ready = [ref for ref in pending if not (deps[ref] - done)]
        if not ready:
            message = "The mirror schema has a circular key reference. Fix the schema, then retry."
            raise ConflictError(message, remediation=message)
        for ref in ready:
            ordered.append(ref)
            pending.remove(ref)
    return tuple(ordered)


def ensure_target_empty(con: StreamConn, refs: Sequence[str]) -> None:
    """Refuse to copy onto a populated target (the swap needs a clean one)."""
    for ref in sorted(refs):
        if ref.rsplit(".", 1)[-1] == "tenants":
            statements = {
                "count": (
                    "SELECT count(*) FROM "
                    + sql_qualified(ref)
                    + " WHERE tenant_id != "
                    + sql_literal(SEED_TENANT_ID)
                )
            }
        else:
            statements = {"count": "SELECT count(*) FROM " + sql_qualified(ref)}
        row = con.execute(statements["count"]).fetchone()
        if row and row[0]:
            raise ConflictError(TARGET_NOT_EMPTY, remediation=TARGET_NOT_EMPTY)


def clear_seed_tenant(con: StreamConn, ref: str) -> None:
    """Drop the schema-seeded default tenant; the copy re-inserts the
    source's own (same well-known id on every deployment)."""
    con.execute(
        "DELETE FROM " + sql_qualified(ref) + " WHERE tenant_id = " + sql_literal(SEED_TENANT_ID)
    )


def attach_copy(
    con: StreamConn, entries: Sequence[TablePlan], order: Sequence[str]
) -> list[dict[str, str | int]]:
    """INSERT ... SELECT per table in FK order: pushdown read over the
    attachment into the DDL-mirrored target (constraints survive)."""
    by_bare = {entry.name.split(".")[-1]: entry for entry in entries}
    report: list[dict[str, str | int]] = []
    for ref in order:
        bare = ref.rsplit(".", 1)[-1]
        entry = by_bare[bare]
        if bare == "tenants":
            clear_seed_tenant(con, f"main.{bare}")
        columns = _columns(con, entry.name)
        transforms = {t.column: t for t in entry.transforms}
        names = ", ".join(quoted_identifier(c) for c in columns)
        parts = ", ".join(_select_parts(columns, transforms, True))
        target_ref = sql_qualified(f"main.{bare}")
        statements = {
            "copy": (
                "INSERT INTO "
                + target_ref
                + " ("
                + names
                + ") SELECT "
                + parts
                + " FROM "
                + sql_qualified(entry.name)
            ),
            "count": "SELECT count(*) FROM " + target_ref,
        }
        con.execute(statements["copy"])
        count = con.execute(statements["count"]).fetchone()
        rows_written = cast("int", count[0]) if count else 0
        report.append({"name": bare, "rows": rows_written})
    return report


def transforms_from_target(
    con: StreamConn, ref: str, existing: Sequence[ColumnTransform]
) -> tuple[ColumnTransform, ...]:
    """Extend a plan entry's transforms with the target's JSON columns.

    The postgres scanner reports jsonb as VARCHAR, so the JSON set is read
    from the DuckDB mirror's DDL and applied to BOTH digest sides.
    """
    named = {t.column for t in existing}
    extra = tuple(
        ColumnTransform(column, 0, "json")
        for column in json_columns(con, ref)
        if column not in named
    )
    return tuple(existing) + extra


def audit_numeric_columns(con: StreamConn, entries: Sequence[TablePlan]) -> list[str]:
    """table.column pairs whose scanner type is DOUBLE (postgres numeric
    over the attachment — the W0 S3 precision audit for the report)."""
    found: list[str] = []
    for entry in entries:
        catalog, schema, bare = entry.name.split(".")  # three-part attach names
        rows = con.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_catalog = ? AND table_schema = ? AND table_name = ?"
            " AND upper(data_type) LIKE '%DOUBLE%' ORDER BY ordinal_position",
            [catalog, schema, bare],
        ).fetchall()
        found.extend(f"{bare}.{row[0]}" for row in rows)
    return sorted(found)
