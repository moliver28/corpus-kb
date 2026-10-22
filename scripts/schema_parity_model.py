"""Data model and normalization for the schema-parity gate.

The gate (scripts/schema_parity_gate.py) parses both migration sets with
sqlglot and folds them into final per-table states. This module holds the
fact model and the declared type-normalization rules.

STATIC ANALYSIS ONLY. sqlglot never transpiles or rewrites runtime SQL
(storage portability program, OSS table: sqlglot is Adopt, static only).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Origin:
    """Where a schema fact was declared: file plus 1-based line."""

    file: str
    line: int

    def where(self) -> str:
        return f"{self.file}:{self.line}"


@dataclass
class ColumnSpec:
    """One folded column state."""

    type: str
    nullable: bool
    default: str
    origin: Origin


@dataclass
class TableSpec:
    """Final state of one table after all forward statements."""

    name: str
    origin: Origin
    columns: dict[str, ColumnSpec] = field(default_factory=dict)
    primary_key: tuple[str, ...] = ()
    uniques: set[tuple[str, ...]] = field(default_factory=set)
    # cols -> (target table, target cols). Referential actions are not kept.
    fks: dict[tuple[str, ...], tuple[str, tuple[str, ...]]] = field(default_factory=dict)
    # column -> backing sequence name (SERIAL, IDENTITY, or nextval default).
    sequences: dict[str, str] = field(default_factory=dict)


@dataclass
class IndexSpec:
    """One plain index. PG-only search indexes never reach the model."""

    table: str
    columns: tuple[str, ...]
    unique: bool
    origin: Origin

    @property
    def role(self) -> tuple[str, tuple[str, ...], bool]:
        """Functional role: same table, columns, and uniqueness."""
        return (self.table, self.columns, self.unique)


@dataclass
class SequenceSpec:
    name: str
    origin: Origin


@dataclass
class SchemaState:
    """Final folded state of one migration set."""

    tables: dict[str, TableSpec] = field(default_factory=dict)
    indexes: dict[tuple[str, tuple[str, ...], bool], IndexSpec] = field(default_factory=dict)
    sequences: dict[str, SequenceSpec] = field(default_factory=dict)

    def merge(self, other: SchemaState) -> None:
        self.tables.update(other.tables)
        self.indexes.update(other.indexes)
        self.sequences.update(other.sequences)


# ---------------------------------------------------------------------------
# Declared type normalization. Each rule is an intentional divergence, per
# the DuckDB translation notes in migrations_duckdb/000_base_schema.sql.
# ---------------------------------------------------------------------------

_SIMPLE_TYPES: dict[str, str] = {
    # UUID -> VARCHAR on DuckDB; values stay canonical uuid text.
    "UUID": "TEXT",
    # JSONB -> JSON; logical map is identical.
    "JSONB": "JSON",
    "JSON": "JSON",
    "VARCHAR": "TEXT",
    "NVARCHAR": "TEXT",
    "CHAR": "TEXT",
    "BPCHAR": "TEXT",
    "STRING": "TEXT",
    "TEXT": "TEXT",
    "INT": "INTEGER",
    "INT4": "INTEGER",
    "INTEGER": "INTEGER",
    "BIGINT": "BIGINT",
    "INT8": "BIGINT",
    "SMALLINT": "SMALLINT",
    "BOOLEAN": "BOOLEAN",
    "BOOL": "BOOLEAN",
    "NUMERIC": "NUMERIC",
    "DECIMAL": "NUMERIC",
    "DOUBLE": "DOUBLE",
    "FLOAT8": "DOUBLE",
    "REAL": "REAL",
    "FLOAT4": "REAL",
    "TIMESTAMPTZ": "TIMESTAMPTZ",
    "TIMESTAMP WITH TIME ZONE": "TIMESTAMPTZ",
    "TIMESTAMP": "TIMESTAMP",
    "DATE": "DATE",
}

_SERIAL_TYPES = {"SERIAL": "INTEGER", "BIGSERIAL": "BIGINT", "SMALLSERIAL": "SMALLINT"}

# PG pgvector/pg_refresh types -> DuckDB FLOAT arrays (spike S3 type map).
_ARRAY_TYPES = {"HALFVEC": "FLOAT", "VECTOR": "FLOAT"}


def normalize_type(raw: str) -> str:
    """Canonical type name, or the raw text when the rule set is silent.

    Declared divergences (with why):
      UUID -> TEXT: DuckDB keeps uuid values as canonical text.
      JSONB -> JSON: same logical map; envelope is byte-stable.
      halfvec(n)/vector(n) -> FLOAT[n]: the shipped DuckDB regime.
      ARRAY<FLOAT>[n] -> FLOAT[n]: sqlglot's duckdb read spelling of the
      same FLOAT array the pg side reaches via halfvec/vector.
      VARCHAR(n) -> TEXT: sqlglot's duckdb read drops length modifiers;
      length is not load-bearing after the UUID -> VARCHAR translation.
      SERIAL(n) -> INTEGER/BIGINT: backed by a sequence on both sides.
    """
    text = re.sub(r"\s+", " ", raw.strip().upper())
    match = re.fullmatch(r"ARRAY<(FLOAT|REAL|DOUBLE)>\s*\[(\d+)\]", text)
    if match:
        return f"FLOAT[{match.group(2)}]"
    match = re.fullmatch(r"(HALFVEC|VECTOR)\s*\((\d+)\)", text)
    if match:
        return f"FLOAT[{match.group(2)}]"
    match = re.fullmatch(r"(SERIAL|BIGSERIAL|SMALLSERIAL)", text)
    if match:
        return _SERIAL_TYPES[match.group(1)]
    match = re.fullmatch(r"(VARCHAR|NVARCHAR|CHAR|BPCHAR|STRING)\s*\(\s*\d+\s*\)", text)
    if match:
        return "TEXT"
    if text in _SIMPLE_TYPES:
        return _SIMPLE_TYPES[text]
    return text


def normalize_default(raw: str) -> str:
    """Canonical default text: lowercase, no spaces, dialect spellings mapped.

    Declared equivalences (with why):
      now() == current_timestamp(): same function, one spelling per dialect.
      '{}'::jsonb == '{}'::json: JSONB maps to JSON (type rule above).
    """
    text = re.sub(r"\s+", "", raw).lower()
    text = text.replace("::jsonb", "::json")
    return re.sub(r"current_timestamp\(\)?", "now()", text)
