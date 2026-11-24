"""DuckDB rows of the migration upgrade matrix (W2 compat spine).

File-based rows that run wherever the package and the anchor snapshot
are reachable: each row rebuilds a store at a past release snapshot,
runs the CURRENT migration runner to head, checks schema parity against
a control file built fresh from the current set, and smokes a chunk
insert plus a graph entity insert. Rows skip where the snapshot is
unreachable (shallow checkout) or predates the DuckDB mirror set. The
DuckDB side needs no extension gating: the mirror set ships no-op stubs
for the extension-gated pair, and the runner applies them plainly.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

pytest.importorskip("duckdb")

from corpus_kb._setup.migrate import (
    _ensure_duckdb_migrations_table,
    duckdb_dsn_to_path,
    run_migrations,
)
from tests import migration_matrix_support as support

#: Sorted anchors (rule 9): one row per (anchor, backend). "head" is the
#: control anchor — a fresh build proves the harness itself is sound.
ANCHORS: tuple[str, ...] = ("corpus-rag-v0.6.0", "head", "u48-granular-history")

_DUCK_CONTROLS: dict[str, str] = {}


def _build_store_at(ref: str, path: Path) -> None:
    import duckdb

    files = support.migration_files(ref, support.DUCKDB_DIR, "DuckDB mirror set")
    con = duckdb.connect(str(path))
    try:
        _ensure_duckdb_migrations_table(con)
        for name in files:
            sql = support.git_show_substituted(ref, f"{support.DUCKDB_DIR}/{name}")
            if sql is None:
                pytest.skip(f"anchor snapshot {ref} is missing mirror {name}")
            con.execute("BEGIN TRANSACTION")
            try:
                con.execute(sql)
                con.execute("INSERT INTO schema_migrations (filename) VALUES (?)", [name])
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
    finally:
        con.close()


def _columns(path: Path) -> set[tuple[str, ...]]:
    import duckdb

    con = duckdb.connect(str(path), read_only=True)
    try:
        rows = con.execute(
            "SELECT table_schema, table_name, column_name, data_type, is_nullable"
            " FROM information_schema.columns WHERE table_schema = 'main'"
        ).fetchall()
    finally:
        con.close()
    return {tuple(str(value) for value in row) for row in rows}


def _tracked(path: Path) -> set[str]:
    import duckdb

    con = duckdb.connect(str(path), read_only=True)
    try:
        rows = con.execute("SELECT filename FROM schema_migrations").fetchall()
    finally:
        con.close()
    return {str(row[0]) for row in rows}


async def _control(tmp_path_factory: pytest.TempPathFactory) -> str:
    """One control file per session, built fresh from the current set."""
    if "dsn" not in _DUCK_CONTROLS:
        path = tmp_path_factory.mktemp("w2m-control") / "control.duckdb"
        dsn = f"duckdb:///{path.as_posix()}"
        await run_migrations(dsn)
        _DUCK_CONTROLS["dsn"] = dsn
    return _DUCK_CONTROLS["dsn"]


async def _smoke(dsn: str, anchor: str) -> None:
    import duckdb

    con = duckdb.connect(duckdb_dsn_to_path(dsn))
    try:
        doc_id = str(uuid.uuid4())
        con.execute(
            "INSERT INTO documents (doc_id, tenant_id, source, source_type) VALUES (?, ?, ?, ?)",
            [doc_id, support.SEED_TENANT, f"w2-matrix-{support.slug(anchor)}", "text"],
        )
        con.execute(
            "INSERT INTO chunks (tenant_id, doc_id, chunk_index, text) VALUES (?, ?, 0, ?)",
            [support.SEED_TENANT, doc_id, "upgrade smoke chunk"],
        )
        con.execute(
            "INSERT INTO entities (tenant_id, name) VALUES (?, ?)",
            [support.SEED_TENANT, f"w2-matrix-{support.slug(anchor)}"],
        )
        chunk_count = con.execute("SELECT count(*) FROM chunks").fetchone()
        entity_count = con.execute("SELECT count(*) FROM entities").fetchone()
    finally:
        con.close()
    assert chunk_count is not None and chunk_count[0] >= 1
    assert entity_count is not None and entity_count[0] >= 1


@pytest.mark.parametrize("anchor", ANCHORS)
async def test_duckdb_anchor_upgrades_to_head(
    anchor: str, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    ref = support.git_ref(anchor)
    path = tmp_path / f"w2m-{support.slug(anchor)}.duckdb"
    _build_store_at(ref, path)
    dsn = f"duckdb:///{path.as_posix()}"
    report = await run_migrations(dsn)
    if anchor == "head":
        assert report.applied_now == []
    assert _tracked(path) == support.packaged_names("migrations_duckdb")
    control_path = Path((await _control(tmp_path_factory)).removeprefix("duckdb:///"))
    assert _columns(path) == _columns(control_path)
    await _smoke(dsn, anchor)
