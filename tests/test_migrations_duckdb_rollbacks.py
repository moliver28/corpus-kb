"""DuckDB rollback pin (issue #592 item 1): mirror of the PG-side pin.

The PG pin (``tests/test_migrations.py``) holds the coding block's
rollbacks to three rules: the files exist and map 1:1 onto forward
twins, the runner never auto-applies them, and the block's chain
executes over the applied set and restores what it dropped. This module
pins the same three rules for the DuckDB mirror set. The rehearsal
stays scoped to the coding block (012-015), exactly like the PG pin:
DuckDB refuses any ALTER on a table that a still-applied table
references, so the core-table ALTER rollbacks (016, 023, 027, 036)
cannot run mid-chain. The runner's tracking rows prove the restore in
the coding-block rehearsal instead.
"""

from __future__ import annotations

import importlib.resources as resources
import re
from pathlib import Path

import pytest

from corpus_kb._setup.migrate import (
    DUCKDB_DSN_PREFIX,
    configured_embedding_dims,
    duckdb_dsn_to_path,
    run_duckdb_migrations,
    substitute_dims,
)

pytest.importorskip("duckdb")

_MIGRATIONS = resources.files("corpus_kb") / "migrations_duckdb"

#: ``rollback_012_coding_schema.sql`` -> ``012_coding_schema.sql``.
_ROLLBACK_NAME = re.compile(r"^rollback_(\d+_.+)\.sql$")
_FORWARD_NAME = re.compile(r"^\d+_.+\.sql$")
#: One ``DROP TABLE [IF EXISTS] [<schema>.]<name>`` statement.
_DROP_TABLE = re.compile(
    r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:[A-Za-z_][A-Za-z0-9_]*\.)?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)

#: The block the PG pin rehearses; the DuckDB set mirrors it file for file.
_CODING_ORDINALS = range(12, 16)


def _rollback_names() -> list[str]:
    """Every DuckDB rollback file name, in reverse runner (ordinal) order."""
    names = [p.name for p in _MIGRATIONS.iterdir() if p.is_file() and _ROLLBACK_NAME.match(p.name)]
    return sorted(names, reverse=True)


def _forward_names() -> list[str]:
    """Every DuckDB forward migration file name, in runner (ordinal) order."""
    names = [p.name for p in _MIGRATIONS.iterdir() if p.is_file() and _FORWARD_NAME.match(p.name)]
    return sorted(names)


def _read(name: str) -> str:
    """One migration file's text, read through the package resource."""
    return (_MIGRATIONS.joinpath(name)).read_text(encoding="utf-8")


def _ordinal(name: str) -> int:
    """The runner ordinal of a rollback file (rollback_012_... -> 12)."""
    match = _ROLLBACK_NAME.match(name)
    assert match is not None
    return int(match.group(1)[:3])


def _live_tables(con: object) -> set[str]:
    """User-table names in the connected database."""
    import duckdb

    assert isinstance(con, duckdb.DuckDBPyConnection)
    return {row[0] for row in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()}


def _dropped_tables(names: list[str]) -> set[str]:
    """The table names the rollback chain drops, parsed from the files."""
    tables: set[str] = set()
    for name in names:
        for statement in _read(name).split(";"):
            match = _DROP_TABLE.search(statement)
            if match:
                tables.add(match.group(1))
    return tables


def test_duckdb_rollbacks_exist_and_stay_runner_exempt() -> None:
    """Every rollback file exists, is named for a forward twin, and is inert.

    The runner only picks up ``^\\d+_`` files. A rollback renamed into the
    forward shape would run on every apply, so the pin holds the rollback
    naming the same way the PG pin does.
    """
    names = _rollback_names()
    assert names, "the DuckDB set carries no rollback files"
    forward = set(_forward_names())
    for name in names:
        twin = _ROLLBACK_NAME.match(name)
        assert twin is not None, f"unnamed rollback: {name}"
        assert not _FORWARD_NAME.match(name), f"runner would auto-apply {name}"
        assert f"{twin.group(1)}.sql" in forward, f"rollback without a twin: {name}"


def test_duckdb_coding_block_chain_restores(tmp_path: Path) -> None:
    """The coding block (012-015) chain executes over the applied set.

    Mirrors the PG-side rehearsal: apply the full set, run the coding
    rollbacks in reverse runner order, prove the dropped coding tables
    are gone, re-apply the twins directly (the PG test's service-gated
    path needs no service here), prove the dropped tables are back, and
    prove the runner then sees the database as unchanged. The runner's
    tracking rows stay untouched (a manual rollback leaves them,
    exactly as on Postgres).
    """
    dsn = f"{DUCKDB_DSN_PREFIX}{(tmp_path / 'corpus.duckdb').as_posix()}"
    run_duckdb_migrations(dsn)

    coding = [name for name in _rollback_names() if _ordinal(name) in _CODING_ORDINALS]
    assert len(coding) == 4, f"the coding block lost a rollback file: {coding}"
    dropped = _dropped_tables(coding)
    assert dropped, "the coding rollbacks drop no tables"

    import duckdb

    con = duckdb.connect(duckdb_dsn_to_path(dsn))
    try:
        for name in coding:
            con.sql(_read(name))
        survivors = sorted(dropped & _live_tables(con))
        assert survivors == [], f"rollback left tables behind: {survivors}"

        # Direct rehearsal of the twins, in runner order (the PG test's
        # service-gated path): the dropped tables must come back cleanly.
        twins: set[str] = set()
        for name in coding:
            twin = _ROLLBACK_NAME.match(name)
            assert twin is not None
            twins.add(f"{twin.group(1)}.sql")
        for name in _forward_names():
            if name in twins:
                con.sql(substitute_dims(_read(name), configured_embedding_dims()))
        missing = sorted(dropped - _live_tables(con))
        assert missing == [], f"re-apply idempotency did not restore: {missing}"

        tracked = {
            row[0] for row in con.execute("SELECT filename FROM schema_migrations").fetchall()
        }
        assert tracked == set(_forward_names()), "tracking rows drifted"
    finally:
        con.close()

    # A runner pass over the rehearsed database stays a clean no-op.
    report = run_duckdb_migrations(dsn)
    assert report.applied_now == [], "the runner re-applied after the rehearsal"
