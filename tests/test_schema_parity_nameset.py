"""Tests for the migration name-set cross-check (issue #230).

Five contracts:
  1. The REAL migration sets pass with a fresh allowlist; the offset
     zone pairs by name (duckdb 031 == pg 030), not by ordinal.
  2. A fabricated one-sided forward name fails and names the offender.
  3. An allowlisted forward asymmetry passes with no stale row.
  4. A fabricated rollback asymmetry fails.
  5. The gate exit path blocks on name drift alone.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from schema_parity_gate import main  # noqa: E402
from schema_parity_nameset import name_set_result  # noqa: E402


def _mkdirs(root: Path) -> tuple[Path, Path]:
    pg = root / "src" / "corpus_kb" / "migrations"
    duck = root / "src" / "corpus_kb" / "migrations_duckdb"
    pg.mkdir(parents=True)
    duck.mkdir(parents=True)
    return pg, duck


def _write(path: Path, sql: str) -> None:
    path.write_text(sql, encoding="utf-8")


def test_real_migration_name_sets_pass() -> None:
    """The shipped trees pair by name; every allowlist row is live."""
    result = name_set_result(REPO_ROOT)
    assert result.undeclared == [], [d.line() for d in result.undeclared]
    assert result.stale == [], [k for k, _ in result.stale]
    assert result.ok
    # The offset zone pairs by name: duckdb 031 is pg 030 (issue #230).
    assert "extraction_cache" in result.pg_forward
    assert "extraction_cache" in result.duck_forward
    assert "statements" in result.pg_rollback
    assert "statements" in result.duck_rollback
    # Exactly the two allowlisted DuckDB-only forward names.
    assert set(result.duck_forward) - set(result.pg_forward) == {
        "base_schema",
        "ingest_runs_source",
    }
    assert "coding_schema" in set(result.pg_rollback) - set(result.duck_rollback)


def test_forward_mismatch_names_the_offender(tmp_path: Path) -> None:
    """A one-sided forward name fails with its kind, side, and file."""
    pg, duck = _mkdirs(tmp_path)
    _write(pg / "001_alpha.sql", "CREATE TABLE alpha (id INT);")
    _write(duck / "001_alpha.sql", "CREATE TABLE alpha (id INT);")
    _write(duck / "002_beta.sql", "CREATE TABLE beta (id INT);")
    result = name_set_result(tmp_path, allowlist={})
    assert not result.ok
    assert [(d.kind, d.side, d.name) for d in result.undeclared] == [("forward", "duckdb", "beta")]
    assert result.undeclared[0].file.endswith("002_beta.sql")


def test_allowlisted_forward_asymmetry_passes(tmp_path: Path) -> None:
    """A declared one-sided forward name passes; the row stays live."""
    pg, duck = _mkdirs(tmp_path)
    _write(pg / "001_alpha.sql", "CREATE TABLE alpha (id INT);")
    _write(duck / "001_alpha.sql", "CREATE TABLE alpha (id INT);")
    _write(duck / "002_beta.sql", "CREATE TABLE beta (id INT);")
    result = name_set_result(
        tmp_path, allowlist={("forward", "duckdb", "beta"): "DuckDB-only by design."}
    )
    assert result.ok
    assert result.stale == []
    assert [(d.kind, d.side, d.name) for d, _ in result.declared] == [("forward", "duckdb", "beta")]


def test_same_name_different_ordinal_passes(tmp_path: Path) -> None:
    """The +1 offset pattern passes because the compare uses name sets."""
    pg, duck = _mkdirs(tmp_path)
    _write(pg / "030_extraction_cache.sql", "CREATE TABLE cache (id INT);")
    _write(pg / "031_statements.sql", "CREATE TABLE stmts (id INT);")
    _write(duck / "031_extraction_cache.sql", "CREATE TABLE cache (id INT);")
    _write(duck / "032_statements.sql", "CREATE TABLE stmts (id INT);")
    result = name_set_result(tmp_path, allowlist={})
    assert result.ok
    assert result.pg_forward == ["extraction_cache", "statements"]
    assert result.duck_forward == ["extraction_cache", "statements"]


def test_rollback_asymmetry_fails(tmp_path: Path) -> None:
    """A rollback present on Postgres only fails the compare."""
    pg, duck = _mkdirs(tmp_path)
    _write(pg / "001_alpha.sql", "CREATE TABLE alpha (id INT);")
    _write(duck / "001_alpha.sql", "CREATE TABLE alpha (id INT);")
    _write(pg / "rollback_001_alpha.sql", "DROP TABLE alpha;")
    result = name_set_result(tmp_path, allowlist={})
    assert not result.ok
    assert [(d.kind, d.side, d.name) for d in result.undeclared] == [("rollback", "pg", "alpha")]
    assert result.undeclared[0].file.endswith("rollback_001_alpha.sql")


def test_unclassified_file_fails(tmp_path: Path) -> None:
    """A file that is neither forward nor rollback shape fails outright."""
    pg, _ = _mkdirs(tmp_path)
    _write(pg / "notes.sql", "-- todo")
    result = name_set_result(tmp_path, allowlist={})
    assert [(d.kind, d.side, d.name) for d in result.undeclared] == [
        ("unclassified", "pg", "notes.sql")
    ]


def test_stale_name_row_fails(tmp_path: Path) -> None:
    """An allowlist row with no live asymmetry is stale and fails."""
    pg, duck = _mkdirs(tmp_path)
    _write(pg / "001_alpha.sql", "CREATE TABLE alpha (id INT);")
    _write(duck / "001_alpha.sql", "CREATE TABLE alpha (id INT);")
    result = name_set_result(tmp_path, allowlist={("rollback", "pg", "ghost"): "Gone."})
    assert result.undeclared == []
    assert result.stale == [(("rollback", "pg", "ghost"), "Gone.")]
    assert not result.ok


def test_gate_main_blocks_on_name_drift(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """main() exits 1 when only the name-set check trips."""
    pg, duck = _mkdirs(tmp_path)
    _write(pg / "001_alpha.sql", "CREATE TABLE alpha (id INT);")
    _write(duck / "001_alpha.sql", "CREATE TABLE alpha (id INT);")
    _write(pg / "rollback_001_alpha.sql", "DROP TABLE alpha;")
    assert main(["--project-root", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "UNDECLARED NAME" in out
    assert "Migration name-set check: FAIL." in out
