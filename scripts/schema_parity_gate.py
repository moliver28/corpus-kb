#!/usr/bin/env python3
"""Schema-parity gate: sqlglot diffs the two migration sets (issue #127).

Parses the Postgres truth (``storage/schema.sql`` + forward files in
``migrations/``) and the DuckDB mirror (``migrations_duckdb/``), folds
each set into one final schema state, and diffs tables, columns, PKs,
uniques, CHECKs, FKs (with referential actions), sequences, and plain
indexes. Undeclared drift exits 1. The allowlist lives in
``schema_parity_drift.py``; STALE entries (declared but no longer real)
also exit 1, so the allowlist cannot outlive its drift.

A second check compares migration NAME sets across the two trees
(``schema_parity_nameset``, issue #230): the same name at a different
ordinal passes by design, a one-sided name fails unless declared.

sqlglot is STATIC ANALYSIS ONLY here, never a runtime transpiler
(storage portability program, OSS table; section 17).

Usage:
    python scripts/schema_parity_gate.py [--project-root PATH]

Exit code 0 = parity holds, 1 = undeclared drift or stale declaration.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from schema_parity_compare import Drift, compare_states
from schema_parity_drift import ALLOWED_DRIFT
from schema_parity_fold import fold_file
from schema_parity_model import SchemaState
from schema_parity_nameset import NameSetResult, name_set_result

ROOT = Path(__file__).resolve().parents[1]
PG_BASE = Path("src/corpus_kb/storage/schema.sql")
PG_MIGRATIONS = Path("src/corpus_kb/migrations")
DUCK_MIGRATIONS = Path("src/corpus_kb/migrations_duckdb")

#: The width placeholder the migration DDL carries (issue #559). Parity
#: compares schema SHAPE, so both sides fold at the shipped default width.
#: Keep in lockstep with corpus_kb._setup.store_width.EMBEDDING_DIMS_PLACEHOLDER.
WIDTH_PLACEHOLDER = "__EMBEDDING_DIMS__"
WIDTH_SUBSTITUTE = "2560"


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
    working past migration 099). The DuckDB set folds its forward files
    in name order; twins pair by name, and numbers 030-042 are offset
    by one (issue #230).
    """
    pg: list[tuple[Path, str]] = []
    base = root / PG_BASE
    if base.exists():
        pg.append((base, PG_BASE.as_posix()))
    for path in sorted((root / PG_MIGRATIONS).glob("[0-9]*.sql")):
        rel = path.relative_to(root)
        pg.append((path, rel.as_posix()))
    # The mirror set folds forward files only (glob ``[0-9]*.sql`` skips the
    # rollback twins), the same rule the Postgres side follows — rollback
    # files run operator-side and never shape the applied schema state.
    duck: list[tuple[Path, str]] = []
    for path in sorted((root / DUCK_MIGRATIONS).glob("[0-9]*.sql")):
        rel = path.relative_to(root)
        duck.append((path, rel.as_posix()))
    return pg, duck


def fold_paths(pairs: list[tuple[Path, str]], dialect: str) -> SchemaState:
    """Fold a (path, display) file list into one schema state.

    The width placeholder is substituted with the shipped default first
    (issue #559): both engines carry the same placeholder, so parity
    compares schema shape at one width, not the configured width.
    """
    state = SchemaState()
    with tempfile.TemporaryDirectory() as tmp:
        for path, display in pairs:
            text = (
                path.read_text(encoding="utf-8")
                .replace(WIDTH_PLACEHOLDER, WIDTH_SUBSTITUTE)
            )
            copy_path = Path(tmp) / path.name
            copy_path.write_text(text, encoding="utf-8")
            fold_file(copy_path, dialect, display, state)
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
    names: NameSetResult = name_set_result(root)
    print(f"Schema parity gate: {len(pg_files)} pg files, {len(duck_files)} duckdb files.")
    if not names.ok:
        for drift in names.undeclared:
            print(f"UNDECLARED NAME {drift.line()}")
        for key, why in names.stale:
            print(f"STALE NAME ROW {key}: {why}")
        print(
            f"Migration name-set check: FAIL. {len(names.undeclared)} undeclared "
            f"name(s), {len(names.stale)} stale row(s)."
        )
        print("Fix the file names or declare each asymmetry in ALLOWED_NAME_ASYMMETRY.")
        return 1
    if result.ok:
        print("Schema parity: PASS.")
        print(f"Declared drift: {len(result.declared)} (see ALLOWED_DRIFT).")
        print(f"Migration name-set check: PASS ({len(names.declared)} declared row(s)).")
        for label, name_list in (
            ("pg forward", names.pg_forward),
            ("duckdb forward", names.duck_forward),
            ("pg rollback", names.pg_rollback),
            ("duckdb rollback", names.duck_rollback),
        ):
            print(f"{label} names ({len(name_list)}): {', '.join(name_list)}")
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
