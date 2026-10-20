"""migrate-data core: stream copy + verified digest (issue #127).

The handler behind the ``corpus-kb migrate-data`` swap (program §3.3, §4,
§9). Skeleton scope for WP sp-migrate-data: driver-agnostic plan/copy/
verify over DBAPI-shaped connections (both sides DuckDB today); the later
work package adds the CLI adapter, the ATTACH postgres leg, and the
event-store leg. Split per program §11 rule 7: ``_copy_table`` /
``_corpus_digest`` / ``_verify_counts``.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from typing import Literal, Protocol

from corpus_kb.errors import ConflictError

Disposition = Literal["copy", "skip", "special"]

# String bank §11.15 — use verbatim on any copy/verify mismatch.
MIGRATION_MISMATCH = (
    "The copied data did not match the source. Fix the cause, then run migrate-data again."
)

STREAM_BATCH = 512
_DIRECTIONS = ("postgres_to_duckdb", "duckdb_to_postgres")


@dataclass(frozen=True)
class ColumnTransform:
    """Re-parse for a text-arrived vector column (W0 S3): the postgres
    scanner delivers halfvec/vector as '[v, v, ...]' VARCHAR."""

    column: str
    dims: int


@dataclass(frozen=True)
class TablePlan:
    """One inventory entry: what to do with a table during the swap."""

    name: str
    disposition: Disposition
    pk: tuple[str, ...] = ()
    transforms: tuple[ColumnTransform, ...] = ()
    reason: str = ""


class StreamConn(Protocol):
    """DBAPI-shaped connection the core copies through (duckdb today)."""

    def execute(self, sql: str, params: Sequence[object] | None = None) -> StreamConn: ...
    def fetchmany(self, size: int) -> Sequence[Sequence[object]]: ...
    def fetchall(self) -> Sequence[Sequence[object]]: ...
    def fetchone(self) -> Sequence[object] | None: ...
    def executemany(self, sql: str, seq: Sequence[Sequence[object]]) -> StreamConn: ...


# Tenant-exemption manifest — frozen from the W0 tenancy audit
# (findings/migration-eventstore.md S1: 40 tables, 37 tenanted; the rest
# are these exemptions). One entry per table, matched by bare name so
# schema-qualified discoveries (main.tenants, corpus.tenants) hit once.
# Drives this inventory, the rule-18 coverage test, and the parity gate.
EXEMPTION_MANIFEST: tuple[TablePlan, ...] = (
    TablePlan("tenants", "copy", pk=("tenant_id",), reason="W0 S1: global (schema.sql)"),
    TablePlan("corpus.schema_migrations", "skip", reason="W0 S1: runner-owned bookkeeping"),
    TablePlan("fts_watermark", "skip", reason="W0 S1: DuckDB-only FTS meta; rebuilt"),
    TablePlan("corpusapplication_events", "special", reason="W0 S1/S4: event-store leg"),
    TablePlan("corpusapplication_snapshots", "special", reason="W0 S1/S4: event-store leg"),
    TablePlan("stored_events", "special", reason="W0 S2: SQLite events naming"),
    TablePlan("stored_snapshots", "special", reason="W0 S2: SQLite snapshots naming"),
    TablePlan("corpus.sources", "special", reason="W0 S1: legacy, outside the mirror"),
    TablePlan("corpus.nodes", "special", reason="W0 S1: legacy, outside the mirror"),
    TablePlan("corpus.index_generations", "special", reason="W0 S1: legacy, out of mirror"),
)

# Text-arrived vector columns per mirrored table (W0 S3 type map). The
# ATTACH leg extends this map from migrations_duckdb; NULL vectors are
# tolerated end to end (W0 surprise 4).
VECTOR_TEXT_TRANSFORMS: dict[str, tuple[ColumnTransform, ...]] = {
    "chunks_vectors": (
        ColumnTransform("embedding", 2560),
        ColumnTransform("vector_1024", 1024),
    ),
}


def discover_tables(conn: StreamConn) -> tuple[str, ...]:
    """Schema-qualified base-table names on one side, in stable order."""
    rows = conn.execute(
        "SELECT table_schema || '.' || table_name FROM information_schema.tables"
        " WHERE table_schema NOT IN ('information_schema', 'pg_catalog')"
        " AND table_type = 'BASE TABLE' ORDER BY 1"
    ).fetchall()
    return tuple(str(row[0]) for row in rows)


def build_plan(discovered: Sequence[str]) -> tuple[TablePlan, ...]:
    """Merge discovered tables with the frozen manifest (rule 18)."""
    manifest = {entry.name.split(".")[-1]: entry for entry in EXEMPTION_MANIFEST}
    plan: list[TablePlan] = []
    for qualified in sorted(discovered):
        bare = qualified.split(".")[-1]
        exempt = manifest.get(bare)
        if exempt is not None:
            plan.append(replace(exempt, name=qualified))
            continue
        transforms = VECTOR_TEXT_TRANSFORMS.get(bare, ())
        entry = TablePlan(qualified, "copy", transforms=transforms, reason="mirrored (§4)")
        plan.append(entry)
    return tuple(plan)


def _columns(conn: StreamConn, table: str) -> tuple[str, ...]:
    schema, name = table.rsplit(".", 1)
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position",
        [schema, name],
    ).fetchall()
    return tuple(str(row[0]) for row in rows)


def _select_parts(
    columns: Sequence[str], transforms: dict[str, ColumnTransform], reparse: bool
) -> list[str]:
    parts = []
    for column in columns:
        quoted = f'"{column}"'
        transform = transforms.get(column) if reparse else None
        if transform is not None:
            cast = f"CAST(replace({quoted}, ' ', '') AS FLOAT[{transform.dims}])"
            quoted = f"{cast} AS {quoted}"
        parts.append(quoted)
    return parts


def _copy_table(source: StreamConn, target: StreamConn, entry: TablePlan) -> int:
    """Stream one table row-batch by row-batch; never whole-table fetchall."""
    columns = _columns(source, entry.name)
    transforms = {t.column: t for t in entry.transforms}
    names = ", ".join(f'"{c}"' for c in columns)
    parts = ", ".join(_select_parts(columns, transforms, True))
    select = f"SELECT {parts} FROM {entry.name}"
    marks = ", ".join("?" for _ in columns)
    insert = f"INSERT INTO {entry.name} ({names}) VALUES ({marks})"
    cursor = source.execute(select)
    copied = 0
    while rows := cursor.fetchmany(STREAM_BATCH):
        target.executemany(insert, rows)
        copied += len(rows)
    return copied


def _canonical(value: object) -> list[object]:
    """Tagged, byte-stable form of one DB value (json.dumps-able)."""
    if value is None:
        return ["n"]
    if isinstance(value, bool):
        return ["b", value]
    if isinstance(value, (int, float)):
        return ["#", repr(value)]
    if isinstance(value, str):
        return ["t", value]
    if isinstance(value, bytes):
        return ["x", value.hex()]
    if isinstance(value, (list, tuple)):
        return ["a", [_canonical(item) for item in value]]
    return ["r", repr(value)]


def _row_lines(conn: StreamConn, sql: str) -> Iterator[str]:
    """Stream canonical row lines for the digest hash."""
    cursor = conn.execute(sql)
    while rows := cursor.fetchmany(STREAM_BATCH):
        for row in rows:
            yield json.dumps([_canonical(value) for value in row], separators=(",", ":"))


def _corpus_digest(
    conn: StreamConn, tables: Sequence[TablePlan], *, transform_text_vectors: bool
) -> str:
    """SHA-256 over ordered (table, pk) rows; pk-declared tables stream in
    SQL order, pk-less tables buffer and Python-sort (the ATTACH leg must
    declare pks for the large vector tables so those stream).
    """
    digest = hashlib.sha256()
    for entry in sorted(tables, key=lambda e: e.name):
        columns = _columns(conn, entry.name)
        transforms = {t.column: t for t in entry.transforms}
        parts = ", ".join(_select_parts(columns, transforms, transform_text_vectors))
        sql = f"SELECT {parts} FROM {entry.name}"
        if entry.pk:
            sql += " ORDER BY " + ", ".join(f'"{c}"' for c in entry.pk)
        lines = _row_lines(conn, sql)
        if not entry.pk:
            lines = iter(sorted(lines))
        table_hash = hashlib.sha256()
        count = 0
        for line in lines:
            table_hash.update(line.encode("utf-8"))
            count += 1
        digest.update(f"{entry.name}\x1e{count}\x1e".encode())
        digest.update(table_hash.digest())
    return digest.hexdigest()


def _verify_counts(source: StreamConn, target: StreamConn, tables: Sequence[TablePlan]) -> None:
    """Per-table row-count check; mismatch raises the bank string."""
    for entry in sorted(tables, key=lambda e: e.name):
        src = source.execute(f"SELECT count(*) FROM {entry.name}").fetchone()
        dst = target.execute(f"SELECT count(*) FROM {entry.name}").fetchone()
        if src != dst:
            raise ConflictError(MIGRATION_MISMATCH, remediation=MIGRATION_MISMATCH)


def migrate(source_conn: StreamConn, target_conn: StreamConn, direction: str) -> dict:
    """Plan, copy, verify counts, verify digest; return the swap report."""
    if direction not in _DIRECTIONS:
        allowed = ", ".join(_DIRECTIONS)
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
    # The vector-text side reads through the plan transforms so both digests
    # hash the logical (FLOAT[n]) form; postgres_to_duckdb has the text side.
    source_is_text = direction == "postgres_to_duckdb"
    source_digest = _corpus_digest(source_conn, copies, transform_text_vectors=source_is_text)
    target_digest = _corpus_digest(target_conn, copies, transform_text_vectors=not source_is_text)
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
