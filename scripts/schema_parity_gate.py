#!/usr/bin/env python3
"""Schema-parity gate: sqlglot diffs the two migration sets (issue #127).

Parses the Postgres truth (``storage/schema.sql`` + forward files in
``migrations/``) and the DuckDB mirror (``migrations_duckdb/``), folds
each set into one final schema state, and diffs tables, columns, PKs,
uniques, FKs, sequences, and plain indexes. Undeclared drift exits 1.
STALE allowlist entries (declared but no longer real) also exit 1, so
the allowlist cannot outlive its drift.

sqlglot is STATIC ANALYSIS ONLY here, never a runtime transpiler
(storage portability program, OSS table; section 17).

Usage:
    python scripts/schema_parity_gate.py [--project-root PATH]

Exit code 0 = parity holds, 1 = undeclared drift or stale declaration.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

from schema_parity_compare import Drift, compare_states
from schema_parity_fold import fold_file
from schema_parity_model import SchemaState

ROOT = Path(__file__).resolve().parents[1]
PG_BASE = Path("src/corpus_kb/storage/schema.sql")
PG_MIGRATIONS = Path("src/corpus_kb/migrations")
DUCK_MIGRATIONS = Path("src/corpus_kb/migrations_duckdb")

# Declared divergences: (kind, table, item) -> one-line why. Anything not
# listed here fails. An entry that no longer matches real drift is STALE
# and fails the gate, so this table stays truthful.
_NUMERIC_TO_DOUBLE = "Unbounded numeric becomes DOUBLE on DuckDB (duckdb 012 header)."
_DYNAMIC_RETYPE = (
    "pg 025 retypes this to halfvec(2560) via dynamic EXECUTE; the static fold cannot see it."
)
_NOT_NULL_ADD = "DuckDB refuses NOT NULL ADD COLUMN; the gateway coalesces NULL."
ALLOWED_DRIFT: dict[tuple[str, str, str], str] = {
    # -- Unbounded numeric -> DOUBLE (duckdb 012 header; W0 spike finding).
    ("column.type", "chunk_codes", "confidence"): _NUMERIC_TO_DOUBLE,
    ("column.type", "chunk_signals", "score"): _NUMERIC_TO_DOUBLE,
    ("column.type", "code_keywords", "ll"): _NUMERIC_TO_DOUBLE,
    ("column.type", "code_keywords", "log_ratio"): _NUMERIC_TO_DOUBLE,
    ("column.type", "code_registry", "pool_floor"): _NUMERIC_TO_DOUBLE,
    ("column.type", "code_registry", "residual_floor"): _NUMERIC_TO_DOUBLE,
    ("column.type", "doc_extraction_audit", "miss_rate_point"): _NUMERIC_TO_DOUBLE,
    ("column.type", "doc_extraction_audit", "miss_rate_ub95"): _NUMERIC_TO_DOUBLE,
    ("column.type", "final_codes", "confidence"): _NUMERIC_TO_DOUBLE,
    # -- pg 025 retypes these to halfvec(2560) with dynamic EXECUTE format;
    # -- the static fold cannot see inside dynamic SQL.
    ("column.type", "answer_ctx_vectors", "vector"): _DYNAMIC_RETYPE,
    ("column.type", "chunk_codes", "rationale_vector"): _DYNAMIC_RETYPE,
    ("column.type", "code_registry", "definition_vector"): _DYNAMIC_RETYPE,
    ("column.type", "code_registry", "probe_vector"): _DYNAMIC_RETYPE,
    # -- DuckDB refuses NOT NULL on ADD COLUMN; the gateway coalesces NULL.
    ("column.nullable", "chunks", "degraded"): _NOT_NULL_ADD,
    ("column.nullable", "chunks_vectors", "degraded"): _NOT_NULL_ADD,
    ("column.nullable", "documents", "doc_version"): _NOT_NULL_ADD,
    ("column.nullable", "answer_ctx_vectors", "vector"): (
        "pg 025 drops this NOT NULL via dynamic EXECUTE; both engines end nullable."
    ),
    # -- Constraint shape differences with a documented owner.
    ("unique", "codebook_releases", "codebook_id,codebook_version,tenant_id"): (
        "pg keeps the one-released-row unique; DuckDB uses a plain index, gateway enforced."
    ),
    ("unique", "relations", "relation_type,source_entity_id,target_entity_id,tenant_id"): (
        "pg 008 replaces this unique in a DO block; the gate cannot attribute the drop."
    ),
    # -- DuckDB-only ledger column and index (duckdb 030, documented there).
    ("column", "ingest_runs", "source"): (
        "DuckDB-only ledger column (030): documents rows freeze, so freshness lives in the ledger."
    ),
    ("index", "ingest_runs", "source,started_at,tenant_id"): "DuckDB-only ledger index (030).",
    ("index", "codebook_releases", "codebook_id,codebook_version,tenant_id"): (
        "Plain lookup twin of the pg unique index; the gateway enforces the rule."
    ),
    # -- Orphan sequence kept for the pg serial naming convention.
    ("sequence", "", "sources_id_seq"): (
        "Orphan: DuckDB 001 mirrors the pg serial name; sources.id uses gen_random_uuid()."
    ),
}


@dataclass
class GateResult:
    """Declared drift, undeclared drift, and stale allowlist entries."""

    declared: list[Drift] = field(default_factory=list)
    undeclared: list[Drift] = field(default_factory=list)
    stale: list[tuple[tuple[str, str, str], str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.undeclared and not self.stale


def migration_files(root: Path) -> tuple[list[tuple[Path, str]], list[tuple[Path, str]]]:
    """(path, display) pairs for both sets, in application order.

    Forward Postgres truth is storage/schema.sql plus the numbered
    migrations (glob ``[0-9]*.sql`` skips the rollback files and keeps
    working past migration 099). The DuckDB set runs 000..030 in name
    order.
    """
    pg: list[tuple[Path, str]] = []
    base = root / PG_BASE
    if base.exists():
        pg.append((base, PG_BASE.as_posix()))
    for path in sorted((root / PG_MIGRATIONS).glob("[0-9]*.sql")):
        rel = path.relative_to(root)
        pg.append((path, rel.as_posix()))
    duck: list[tuple[Path, str]] = []
    for path in sorted((root / DUCK_MIGRATIONS).glob("*.sql")):
        rel = path.relative_to(root)
        duck.append((path, rel.as_posix()))
    return pg, duck


def fold_paths(pairs: list[tuple[Path, str]], dialect: str) -> SchemaState:
    """Fold a (path, display) file list into one schema state."""
    state = SchemaState()
    for path, display in pairs:
        fold_file(path, dialect, display, state)
    return state


def evaluate(
    pg: SchemaState, duck: SchemaState, allowlist: dict[tuple[str, str, str], str] = ALLOWED_DRIFT
) -> GateResult:
    """Split real drift into declared and undeclared, and find stale rows."""
    result = GateResult()
    seen: set[tuple[str, str, str]] = set()
    for drift in compare_states(pg, duck):
        why = allowlist.get(drift.key)
        if why is None:
            result.undeclared.append(drift)
        else:
            seen.add(drift.key)
            result.declared.append(drift)
    for key, why in allowlist.items():
        if key not in seen:
            result.stale.append((key, why))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    root: Path = args.project_root
    pg_files, duck_files = migration_files(root)
    try:
        pg = fold_paths(pg_files, "postgres")
        duck = fold_paths(duck_files, "duckdb")
    except Exception as err:  # SchemaParityError or an unreadable file
        print(f"Schema parity gate: FAIL. {err}")
        return 1
    result = evaluate(pg, duck)
    print(f"Schema parity gate: {len(pg_files)} pg files, {len(duck_files)} duckdb files.")
    if result.ok:
        print("Schema parity: PASS.")
        print(f"Declared drift: {len(result.declared)} (see ALLOWED_DRIFT).")
        return 0
    for drift in result.undeclared:
        print(f"UNDECLARED {drift.line()}")
    for key, why in result.stale:
        print(f"STALE ALLOWLIST ROW {key}: {why}")
    print(
        f"Schema parity: FAIL. {len(result.undeclared)} undeclared drift(s), "
        f"{len(result.stale)} stale declaration(s)."
    )
    print("Fix the migrations or declare the difference in ALLOWED_DRIFT.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
