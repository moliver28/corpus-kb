"""Digest machinery for migrate-data (issue #127, program §3.3/§4).

The digest is the round-trip contract: SHA-256 over ordered (table, pk)
canonical row lines, plus the event store. One canonical form per logical
value on BOTH engines:

- vector-text columns (postgres halfvec/vector arrive as '[v, v, ...]'
  VARCHAR over the scanner) re-parse to their FLOAT[n] logical form, and
  the elements quantize through IEEE half storage — pgvector halfvec
  rounds on write, so both engines must hash the half-rounded value;
- JSON-typed columns canonicalize through json.loads plus a sorted
  compact dump (postgres jsonb normalizes key order and spacing on
  write; the DuckDB JSON column preserves the copied text verbatim);
- UUIDs hash as their dashed text form (the DuckDB mirror's VARCHAR);
- postgres NUMERIC arrives as DOUBLE over the scanner and Decimal on
  read-back; both hash through float() (the documented precision policy).

Import direction (program §11 rule 5): migrate_digest -> migrate_data
primitives. The driver in migrate_data imports corpus_digest lazily so
the edge stays one-way.
"""

from __future__ import annotations

import hashlib
import json
import struct
import uuid
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal

from corpus_kb.handlers.migrate_data import (
    STREAM_BATCH,
    StreamConn,
    TablePlan,
    _columns,
    _select_parts,
)

#: Event-store digest record: (originator hex, version, topic, state bytes).
EventRecord = tuple[str, int, str, bytes]


def _canonical(value: object) -> list[object]:
    """Tagged, byte-stable form of one DB value (json.dumps-able)."""
    if value is None:
        return ["n"]
    if isinstance(value, bool):
        return ["b", value]
    if isinstance(value, uuid.UUID):
        return ["t", str(value)]
    if isinstance(value, Decimal):
        return _canonical(float(value))
    if isinstance(value, memoryview):
        return ["x", bytes(value).hex()]
    if isinstance(value, datetime):
        # One zone for both engines: duckdb reads TIMESTAMPTZ in the
        # local zone, psycopg in UTC — the instant must hash equal.
        return ["t", value.astimezone(UTC).isoformat()]
    if isinstance(value, date):
        return ["t", value.isoformat()]
    if isinstance(value, (int, float)):
        return ["#", repr(value)]
    if isinstance(value, str):
        return ["t", value]
    if isinstance(value, bytes):
        return ["x", value.hex()]
    if isinstance(value, (list, tuple)):
        return ["a", [_canonical(item) for item in value]]
    return ["r", repr(value)]


def _half_quantize(values: Sequence[float]) -> list[float]:
    """Round each float through IEEE half storage (pgvector halfvec policy)."""
    if not values:
        return []
    packed = struct.pack(f"{len(values)}e", *values)
    return list(struct.unpack(f"{len(values)}e", packed))


def _parse_vector_text(text: str) -> list[float]:
    """'[v, v, ...]' (scanner or pgvector output) to a float list."""
    body = str(text).strip().strip("[]")
    return [float(part) for part in body.split(",") if part.strip()]


def _canonical_json(value: object) -> object:
    """Sorted compact JSON text — jsonb and copied text hash equal."""
    return json.dumps(json.loads(str(value)), sort_keys=True, separators=(",", ":"))


def _post_steps(
    entry: TablePlan, columns: Sequence[str]
) -> tuple[Callable[[object], object] | None, ...]:
    """Per-column canonicalization over the fetched value (None = as-is)."""
    transforms = {t.column: t for t in entry.transforms}
    steps: list[Callable[[object], object] | None] = []
    for column in columns:
        transform = transforms.get(column)
        if transform is None:
            steps.append(None)
        elif transform.kind == "json":
            steps.append(lambda v: None if v is None else _canonical_json(v))
        else:

            def _vector(v: object) -> object:
                if v is None:
                    return None
                values = _parse_vector_text(v) if isinstance(v, str) else list(v)
                return _half_quantize(values)

            steps.append(_vector)
    return tuple(steps)


def _row_lines(
    conn: StreamConn,
    sql: str,
    columns: Sequence[str],
    posts: Sequence[Callable[[object], object] | None] = (),
) -> Iterator[str]:
    """Stream canonical row lines for the digest hash: sorted
    (column, value) pairs, so the postgres physical order (ALTER-history)
    and the mirror's end-state DDL order hash equal."""
    cursor = conn.execute(sql)
    while rows := cursor.fetchmany(STREAM_BATCH):
        for row in rows:
            pairs = sorted(
                [name, post(v) if post is not None else v]
                for name, post, v in zip(columns, posts, row, strict=True)
            )
            yield json.dumps(
                [[name, _canonical(value)] for name, value in pairs], separators=(",", ":")
            )


def corpus_digest(
    conn: StreamConn,
    tables: Sequence[TablePlan],
    *,
    transform_text_vectors: bool,
    python_parse: bool = False,
) -> str:
    """SHA-256 over ordered (table, pk) rows; pk-declared tables stream in
    SQL order, pk-less tables buffer and Python-sort.

    python_parse re-parses vector text in Python instead of with a
    FLOAT[n] SQL cast — the postgres side has no FLOAT[n] type.
    """
    digest = hashlib.sha256()
    for entry in sorted(tables, key=lambda e: e.name):
        columns = _columns(conn, entry.name)
        transforms = {t.column: t for t in entry.transforms}
        reparse = transform_text_vectors and not python_parse
        parts = ", ".join(_select_parts(columns, transforms, reparse))
        sql = f"SELECT {parts} FROM {entry.name}"
        if entry.pk:
            sql += " ORDER BY " + ", ".join(f'"{c}"' for c in entry.pk)
        lines = _row_lines(conn, sql, columns, _post_steps(entry, columns))
        if not entry.pk:
            lines = iter(sorted(lines))
        table_hash = hashlib.sha256()
        count = 0
        for line in lines:
            table_hash.update(line.encode("utf-8"))
            count += 1
        # The BARE name prefixes the table hash: the two legs of a swap
        # qualify the same table differently (migrate_src.public.tenants
        # vs main.tenants) and must hash equal.
        digest.update(f"{entry.name.split('.')[-1]}\x1e{count}\x1e".encode())
        digest.update(table_hash.digest())
    return digest.hexdigest()


def event_digest(events: Sequence[EventRecord], snapshots: Sequence[EventRecord]) -> str:
    """SHA-256 over the logical event store, notification-id-free.

    Events hash sorted by (originator, version); snapshots follow. The
    per-store notification id is a local sequence on each backend, so the
    digest covers only the fields the stores share.
    """
    digest = hashlib.sha256()
    for label, records in (("events", events), ("snapshots", snapshots)):
        digest.update(f"{label}\x1e{len(records)}\x1e".encode())
        table_hash = hashlib.sha256()
        for originator, version, topic, state in sorted(records):
            line = json.dumps(
                [
                    ["t", originator],
                    ["#", repr(version)],
                    ["t", topic],
                    ["x", bytes(state).hex()],
                ],
                separators=(",", ":"),
            )
            table_hash.update(line.encode("utf-8"))
        digest.update(table_hash.digest())
    return digest.hexdigest()


def compose_digest(corpus: str, events: str) -> str:
    """One round-trip digest from the corpus digest and the event digest."""
    combined = hashlib.sha256()
    combined.update(corpus.encode("utf-8"))
    combined.update(b"\x1e")
    combined.update(events.encode("utf-8"))
    return combined.hexdigest()
