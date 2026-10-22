"""Tests for the sqlglot schema-parity gate (storage portability, issue #127).

Three contracts:
  1. The REAL migration sets pass the gate with a fresh allowlist. This
     pins current parity and keeps every allowlist row honest.
  2. Synthetic drift (a column on one side only, or a type change) is
     detected as undeclared.
  3. A declared difference passes, and a stale declaration fails.

The gate is driven through its functions (importable module), not via
subprocess; main() gets one exit-code check.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from schema_parity_compare import compare_states  # noqa: E402
from schema_parity_gate import (  # noqa: E402
    ALLOWED_DRIFT,
    evaluate,
    fold_paths,
    main,
    migration_files,
)
from schema_parity_model import normalize_default, normalize_type  # noqa: E402


def _real_states():
    pg_files, duck_files = migration_files(REPO_ROOT)
    assert pg_files, "no Postgres truth files found"
    assert duck_files, "no DuckDB migration files found"
    return fold_paths(pg_files, "postgres"), fold_paths(duck_files, "duckdb")


def test_real_migration_sets_pass_with_fresh_allowlist() -> None:
    """The shipped migration sets have zero undeclared and zero stale drift."""
    pg, duck = _real_states()
    result = evaluate(pg, duck)
    assert result.undeclared == [], [d.line() for d in result.undeclared]
    assert result.stale == [], [k for k, _ in result.stale]
    assert result.ok


def test_normalize_rules_pin_declared_translations() -> None:
    """The declared type and default equivalences stay in force."""
    assert normalize_type("halfvec(2560)") == "FLOAT[2560]"
    assert normalize_type("vector(1024)") == "FLOAT[1024]"
    assert normalize_type("ARRAY<FLOAT>[1024]") == "FLOAT[1024]"
    assert normalize_type("UUID") == "TEXT"
    assert normalize_type("VARCHAR(64)") == "TEXT"
    assert normalize_type("SERIAL") == "INTEGER"
    assert normalize_default("CURRENT_TIMESTAMP() + INTERVAL '24' hours") == (
        "now()+interval'24'hours"
    )


def _write_pair(tmp_path: Path, pg_sql: str, duck_sql: str) -> tuple[Path, Path]:
    pg_file = tmp_path / "pg_truth.sql"
    duck_file = tmp_path / "duck_mirror.sql"
    pg_file.write_text(pg_sql, encoding="utf-8")
    duck_file.write_text(duck_sql, encoding="utf-8")
    return pg_file, duck_file


_PG_WIDGET = (
    "CREATE TABLE widgets (\n"
    "    id UUID PRIMARY KEY,\n"
    "    name TEXT NOT NULL,\n"
    "    extra INT NOT NULL DEFAULT 0\n"
    ");\n"
)

_DUCK_WIDGET = "CREATE TABLE widgets (\n    id VARCHAR PRIMARY KEY,\n    name TEXT NOT NULL\n);\n"


def test_synthetic_missing_column_is_detected(tmp_path: Path) -> None:
    """A column present on Postgres only shows up as undeclared drift."""
    pg_file, duck_file = _write_pair(tmp_path, _PG_WIDGET, _DUCK_WIDGET)
    pg = fold_paths([(pg_file, "pg_truth.sql")], "postgres")
    duck = fold_paths([(duck_file, "duck_mirror.sql")], "duckdb")
    result = evaluate(pg, duck, allowlist={})
    # A missing column reports once; attribute drift needs both sides.
    assert [d.key for d in result.undeclared] == [("column", "widgets", "extra")]
    assert not result.ok


def test_synthetic_type_drift_is_detected(tmp_path: Path) -> None:
    """A silent type change (BIGINT vs INT) is undeclared drift."""
    pg_file, duck_file = _write_pair(
        tmp_path,
        "CREATE TABLE gadgets (id INT PRIMARY KEY, size BIGINT NOT NULL);\n",
        "CREATE TABLE gadgets (id INT PRIMARY KEY, size INT NOT NULL);\n",
    )
    pg = fold_paths([(pg_file, "pg_truth.sql")], "postgres")
    duck = fold_paths([(duck_file, "duck_mirror.sql")], "duckdb")
    result = evaluate(pg, duck, allowlist={})
    assert ("column.type", "gadgets", "size") in [d.key for d in result.undeclared]


def test_allowlist_entry_suppresses_declared_drift(tmp_path: Path) -> None:
    """A declared difference passes; the WHY text rides along."""
    pg_file, duck_file = _write_pair(tmp_path, _PG_WIDGET, _DUCK_WIDGET)
    pg = fold_paths([(pg_file, "pg_truth.sql")], "postgres")
    duck = fold_paths([(duck_file, "duck_mirror.sql")], "duckdb")
    result = evaluate(
        pg,
        duck,
        allowlist={
            ("column", "widgets", "extra"): "DuckDB refuses NOT NULL ADD COLUMN.",
        },
    )
    assert result.undeclared == []
    assert result.stale == []
    assert result.ok
    assert {d.key for d in result.declared} == {("column", "widgets", "extra")}


def test_stale_allowlist_entry_fails(tmp_path: Path) -> None:
    """A declaration with no matching drift is stale and fails the gate."""
    pg_file, duck_file = _write_pair(tmp_path, _PG_WIDGET, _DUCK_WIDGET)
    pg = fold_paths([(pg_file, "pg_truth.sql")], "postgres")
    duck = fold_paths([(duck_file, "duck_mirror.sql")], "duckdb")
    result = evaluate(
        pg,
        duck,
        allowlist={("column", "ghosts", "absent_column"): "Nobody makes this column."},
    )
    assert result.undeclared  # the widgets column stays undeclared
    assert (("column", "ghosts", "absent_column"), "Nobody makes this column.") in (result.stale)
    assert not result.ok


def test_main_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """main() exits 0 on the real repo and 1 on a drifted root."""
    assert main([]) == 0
    assert "Schema parity: PASS." in capsys.readouterr().out

    drifted = tmp_path / "repo"
    (drifted / "src" / "corpus_kb" / "migrations").mkdir(parents=True)
    (drifted / "src" / "corpus_kb" / "migrations_duckdb").mkdir(parents=True)
    (drifted / "src" / "corpus_kb" / "storage").mkdir(parents=True)
    pg_sql = drifted / "src" / "corpus_kb" / "storage" / "schema.sql"
    duck_sql = drifted / "src" / "corpus_kb" / "migrations_duckdb" / "000_base.sql"
    pg_pg, duck_pg = _write_pair(drifted, _PG_WIDGET, _DUCK_WIDGET)
    pg_sql.write_text(pg_pg.read_text(encoding="utf-8"), encoding="utf-8")
    duck_sql.write_text(duck_pg.read_text(encoding="utf-8"), encoding="utf-8")
    assert main(["--project-root", str(drifted)]) == 1
    assert "UNDECLARED" in capsys.readouterr().out


def test_allowlist_covers_every_real_drift_kind() -> None:
    """The shipped allowlist is non-empty and every WHY is a real sentence."""
    pg, duck = _real_states()
    declared = {d.key for d in compare_states(pg, duck)}
    assert declared, "no real drift found; the allowlist keys below are untestable"
    assert set(ALLOWED_DRIFT) == declared
    for why in ALLOWED_DRIFT.values():
        assert why.strip().endswith("."), f"WHY must be a full sentence: {why!r}"
