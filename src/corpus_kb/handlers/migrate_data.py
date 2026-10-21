"""migrate-data core: plan, stream copy, count verify (issue #127).

The handler behind the ``corpus-kb migrate-data`` swap (program §3.3, §4,
§9): driver-agnostic plan/copy/verify over DBAPI-shaped connections.
Split per program §11 rule 7: digest in ``migrate_digest`` (lazy import
below keeps the edge one-way), ATTACH leg in ``migrate_attach``, the
event-store leg in ``migrate_events``, the postgres stream leg in
``migrate_stream``, the both-directions driver in ``migrate_roundtrip``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
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
    """Re-parse for a text-arrived column (W0 S3). kind "vector": the
    scanner delivers halfvec/vector as '[v, v, ...]' VARCHAR. kind "json":
    jsonb arrives as text and normalizes key order and spacing on write."""

    column: str
    dims: int = 0
    kind: Literal["vector", "json"] = "vector"


@dataclass(frozen=True)
class TablePlan:
    """One inventory entry: what to do with a table during the swap."""

    name: str
    disposition: Disposition
    pk: tuple[str, ...] = ()
    transforms: tuple[ColumnTransform, ...] = ()
    reason: str = ""


class StreamConn(Protocol):
    """DBAPI-shaped connection the core copies through: execute returns
    the connection (chaining), the fetch* methods read results,
    executemany runs one statement per tuple. The postgres stream leg
    adapts psycopg to this same shape."""

    def execute(self, sql: str, params: Sequence[object] | None = None) -> StreamConn:
        raise NotImplementedError

    def fetchmany(self, size: int) -> Sequence[Sequence[object]]:
        raise NotImplementedError

    def fetchall(self) -> Sequence[Sequence[object]]:
        raise NotImplementedError

    def fetchone(self) -> Sequence[object] | None:
        raise NotImplementedError

    def executemany(self, sql: str, seq: Sequence[Sequence[object]]) -> StreamConn:
        raise NotImplementedError


# Tenant-exemption manifest — frozen from the W0 tenancy audit
# (findings/migration-eventstore.md S1). Matched by bare name so
# schema-qualified discoveries (main.tenants, corpus.tenants) hit once.
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

# Text-arrived vector columns per mirrored table (W0 S3 type map); NULL
# vectors are tolerated end to end (W0 surprise 4).
VECTOR_TEXT_TRANSFORMS: dict[str, tuple[ColumnTransform, ...]] = {
    "chunks_vectors": (
        ColumnTransform("embedding", 2560),
        ColumnTransform("vector_1024", 1024),
    ),
}


def discover_tables(conn: StreamConn, catalog: str | None = None) -> tuple[str, ...]:
    """Base-table names on one side, in stable order.

    catalog=None scopes the default catalog ('schema.table'). A named
    catalog (the ATTACH leg's read-only alias) yields three-part names so
    attached tables cannot collide with the connection's own database.
    """
    if catalog is None:
        rows = conn.execute(
            "SELECT table_schema || '.' || table_name FROM information_schema.tables"
            " WHERE table_catalog = current_database()"
            " AND table_schema NOT IN ('information_schema', 'pg_catalog')"
            " AND table_type = 'BASE TABLE' ORDER BY 1"
        ).fetchall()
        return tuple(str(row[0]) for row in rows)
    rows = conn.execute(
        "SELECT table_catalog || '.' || table_schema || '.' || table_name"
        " FROM information_schema.tables"
        " WHERE table_catalog = ?"
        " AND table_schema NOT IN ('information_schema', 'pg_catalog')"
        " AND table_type = 'BASE TABLE' ORDER BY 1",
        [catalog],
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
    parts = table.split(".")
    if len(parts) == 3:
        catalog, schema, name = parts
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_catalog = ? AND table_schema = ? AND table_name = ?"
            " ORDER BY ordinal_position",
            [catalog, schema, name],
        ).fetchall()
    else:
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
        if transform is not None and transform.kind == "vector":
            cast = f"CAST(replace({quoted}, ' ', '') AS FLOAT[{transform.dims}])"
            quoted = f"{cast} AS {quoted}"
        parts.append(quoted)
    return parts


def _copy_table(
    source: StreamConn,
    target: StreamConn,
    entry: TablePlan,
    *,
    reparse: bool = True,
    transform_row: Callable[[Sequence[object]], Sequence[object]] | None = None,
    target_name: str | None = None,
) -> int:
    """Stream one table row-batch by row-batch; never whole-table fetchall.

    reparse=False reads vector columns raw (already FLOAT[n] lists on the
    DuckDB source side). transform_row maps each row before insert (the
    postgres target side formats float lists as '[v, ...]' text).
    target_name overrides the insert target when the two legs qualify the
    table differently (the stream leg's 'main.x' source vs 'public.x').
    """
    columns = _columns(source, entry.name)
    transforms = {t.column: t for t in entry.transforms}
    names = ", ".join(f'"{c}"' for c in columns)
    parts = ", ".join(_select_parts(columns, transforms, reparse))
    select = f"SELECT {parts} FROM {entry.name}"
    marks = ", ".join("?" for _ in columns)
    insert = f"INSERT INTO {target_name or entry.name} ({names}) VALUES ({marks})"
    cursor = source.execute(select)
    copied = 0
    while rows := cursor.fetchmany(STREAM_BATCH):
        if transform_row is not None:
            rows = [tuple(transform_row(row)) for row in rows]
        target.executemany(insert, rows)
        copied += len(rows)
    return copied


def _corpus_digest(
    conn: StreamConn, tables: Sequence[TablePlan], *, transform_text_vectors: bool
) -> str:
    """Back-compat alias: the digest machinery lives in migrate_digest."""
    from corpus_kb.handlers.migrate_digest import corpus_digest

    return corpus_digest(conn, tables, transform_text_vectors=transform_text_vectors)


def _verify_counts(source: StreamConn, target: StreamConn, tables: Sequence[TablePlan]) -> None:
    """Per-table row-count check; mismatch raises the bank string."""
    for entry in sorted(tables, key=lambda e: e.name):
        src = source.execute(f"SELECT count(*) FROM {entry.name}").fetchone()
        dst = target.execute(f"SELECT count(*) FROM {entry.name}").fetchone()
        if src != dst:
            raise ConflictError(MIGRATION_MISMATCH, remediation=MIGRATION_MISMATCH)


def migrate(source_conn: StreamConn, target_conn: StreamConn, direction: str) -> dict:
    """Hermetic duckdb-to-duckdb swap; the real driver is migrate_roundtrip."""
    from corpus_kb.handlers.migrate_roundtrip import duckdb_swap

    return duckdb_swap(source_conn, target_conn, direction)
