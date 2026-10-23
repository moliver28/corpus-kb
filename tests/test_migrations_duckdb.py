"""DuckDB migration mirror: hermetic runner and schema tests.

Covers the sp-duckdb-migrations done-when list:
- the full set applies once, then applies twice cleanly (idempotence);
- a row round-trips through chunks / chunks_vectors / entities / relations
  with a 2560-float embedding, JSON metadata text, and uuid-string keys;
- every table in the Postgres migrations has a DuckDB counterpart (the
  seed of the schema-parity manifest);
- the 12 coding-domain tables exist.

All tests are in-process (tmp_path files); no server, no extensions.
"""

from __future__ import annotations

import importlib.resources as resources
import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from corpus_kb._setup.migrate import (
    DUCKDB_DSN_PREFIX,
    duckdb_dsn_to_path,
    run_duckdb_migrations,
    run_migrations,
)

if TYPE_CHECKING:
    import duckdb

pytest.importorskip("duckdb")

#: Forward PG migration files, sorted (rollback_* excluded by the digits rule).
_PG_MIGRATIONS = sorted(
    p
    for p in (resources.files("corpus_kb") / "migrations").iterdir()
    if p.is_file() and p.suffix == ".sql" and re.match(r"^\d+_", p.name)
)

_DUCKDB_MIGRATIONS = sorted(
    p
    for p in (resources.files("corpus_kb") / "migrations_duckdb").iterdir()
    if p.is_file() and p.suffix == ".sql" and re.match(r"^\d+_", p.name)
)

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_.]*)",
    re.IGNORECASE,
)

_CODING_TABLES = [
    "code_registry",
    "codebook_versions",
    "code_keywords",
    "chunk_signals",
    "chunk_keyword_hits",
    "answer_ctx_vectors",
    "batch_status",
    "chunk_codes",
    "final_codes",
    "chunk_status",
    "doc_extraction_audit",
    "coding_gate_decisions",
]


def _dsn(tmp_path: Path) -> str:
    """DuckDB DSN for a per-test database file (Windows drive letter safe)."""
    return f"{DUCKDB_DSN_PREFIX}{(tmp_path / 'corpus.duckdb').as_posix()}"


def _connect(dsn: str) -> duckdb.DuckDBPyConnection:
    import duckdb

    return duckdb.connect(duckdb_dsn_to_path(dsn))


def _connect_read_only(dsn: str) -> duckdb.DuckDBPyConnection:
    import duckdb

    return duckdb.connect(duckdb_dsn_to_path(dsn), read_only=True)


def _table_names(con: duckdb.DuckDBPyConnection) -> set[str]:
    rows = con.execute("SELECT table_name FROM duckdb_tables() WHERE NOT internal").fetchall()
    return {row[0] for row in rows}


def _applied_names(con: duckdb.DuckDBPyConnection) -> set[str]:
    return {row[0] for row in con.execute("SELECT filename FROM schema_migrations").fetchall()}


@pytest.fixture()
def migrated_dsn(tmp_path: Path) -> str:
    """A database file with the full DuckDB migration set applied once."""
    dsn = _dsn(tmp_path)
    run_duckdb_migrations(dsn)
    return dsn


# ---------------------------------------------------------------------------
# DSN helper
# ---------------------------------------------------------------------------


def test_duckdb_dsn_to_path_strips_windows_drive() -> None:
    assert duckdb_dsn_to_path("duckdb:///C:/data/corpus.duckdb") == "C:/data/corpus.duckdb"


def test_duckdb_dsn_to_path_keeps_relative_and_posix_forms() -> None:
    assert duckdb_dsn_to_path("duckdb:///data/corpus.duckdb") == "data/corpus.duckdb"
    assert duckdb_dsn_to_path("duckdb:////abs/corpus.duckdb") == "/abs/corpus.duckdb"


def test_duckdb_dsn_to_path_rejects_non_duckdb_dsn() -> None:
    with pytest.raises(ValueError):
        duckdb_dsn_to_path("postgresql://user:pass@localhost:5432/corpus_kb")


# ---------------------------------------------------------------------------
# Runner: apply once, apply twice, backend dispatch
# ---------------------------------------------------------------------------


def test_full_migration_set_applies_once(tmp_path: Path) -> None:
    dsn = _dsn(tmp_path)
    run_duckdb_migrations(dsn)

    expected = {p.name for p in _DUCKDB_MIGRATIONS}
    assert len(expected) >= 30, "the mirror must cover 000..029"

    con = _connect_read_only(dsn)
    try:
        assert _applied_names(con) == expected
    finally:
        con.close()


def test_migrations_apply_twice_cleanly(tmp_path: Path) -> None:
    dsn = _dsn(tmp_path)
    run_duckdb_migrations(dsn)
    # A second run is a no-op: same tracking rows, same table count.
    run_duckdb_migrations(dsn)

    con = _connect_read_only(dsn)
    try:
        assert _applied_names(con) == {p.name for p in _DUCKDB_MIGRATIONS}
        assert len(_table_names(con)) == 58  # 57 mirrored + schema_migrations
    finally:
        con.close()


async def test_run_migrations_dispatches_duckdb_dsn(tmp_path: Path) -> None:
    dsn = _dsn(tmp_path)
    await run_migrations(dsn)  # duckdb: prefix routes to the duckdb runner

    con = _connect_read_only(dsn)
    try:
        assert _applied_names(con) == {p.name for p in _DUCKDB_MIGRATIONS}
    finally:
        con.close()


def test_full_set_applies_to_a_file_named_corpus_dot_duckdb(tmp_path: Path) -> None:
    # Regression: DuckDB names a database after its file stem. On a file
    # named corpus.duckdb every corpus.<table> reference used to bind
    # ambiguously; the mirror keeps table names unqualified so any file
    # name works.
    dsn = f"{DUCKDB_DSN_PREFIX}{(tmp_path / 'corpus.duckdb').as_posix()}"
    run_duckdb_migrations(dsn)

    con = _connect_read_only(dsn)
    try:
        assert _applied_names(con) == {p.name for p in _DUCKDB_MIGRATIONS}
        names = _table_names(con)
        assert {"sources", "nodes", "index_generations"} <= names
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Row round-trip through the core tables
# ---------------------------------------------------------------------------


def test_core_rows_round_trip(migrated_dsn: str) -> None:
    tenant = "00000000-0000-0000-0000-000000000001"
    doc_id = "11111111-1111-1111-1111-111111111111"
    chunk_id = "22222222-2222-2222-2222-222222222222"
    entity_id = "33333333-3333-3333-3333-333333333333"
    other_entity = "33333333-3333-3333-3333-333333333334"
    relation_id = "44444444-4444-4444-4444-444444444444"
    metadata_text = '{"z": 1, "a": [2, 3], "keep": null, "key order": true}'
    # FLOAT[2560] stores 32-bit floats; use values that are exact in binary.
    embedding = [(i % 8) * 0.25 - 1.0 for i in range(2560)]

    con = _connect(migrated_dsn)
    try:
        con.execute("BEGIN TRANSACTION")
        con.execute(
            "INSERT INTO documents (doc_id, tenant_id, source, metadata) VALUES (?, ?, ?, ?)",
            [doc_id, tenant, "round-trip", metadata_text],
        )
        con.execute(
            "INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text,"
            " heading_path, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [chunk_id, tenant, doc_id, 0, "chunk body", '["top", "sub"]', metadata_text],
        )
        con.execute(
            "INSERT INTO chunks_vectors (chunk_id, tenant_id, embedding, dimensions)"
            " VALUES (?, ?, ?, ?)",
            [chunk_id, tenant, embedding, 2560],
        )
        con.execute(
            "INSERT INTO entities (entity_id, tenant_id, name, metadata) VALUES (?, ?, ?, ?)",
            [entity_id, tenant, "alpha", metadata_text],
        )
        con.execute(
            "INSERT INTO entities (entity_id, tenant_id, name) VALUES (?, ?, ?)",
            [other_entity, tenant, "beta"],
        )
        con.execute(
            "INSERT INTO relations (relation_id, tenant_id, source_entity_id,"
            " target_entity_id, chunk_id, metadata) VALUES (?, ?, ?, ?, ?, ?)",
            [relation_id, tenant, entity_id, other_entity, chunk_id, metadata_text],
        )
        con.execute("COMMIT")

        doc = con.execute(
            "SELECT doc_id, metadata FROM documents WHERE doc_id = ?", [doc_id]
        ).fetchone()
        assert doc == (doc_id, metadata_text)

        chunk = con.execute(
            "SELECT chunk_id, heading_path, metadata FROM chunks WHERE chunk_id = ?",
            [chunk_id],
        ).fetchone()
        assert chunk == (chunk_id, '["top", "sub"]', metadata_text)

        row = con.execute(
            "SELECT chunk_id, embedding, dimensions FROM chunks_vectors WHERE chunk_id = ?",
            [chunk_id],
        ).fetchone()
        assert row[0] == chunk_id
        assert len(row[1]) == 2560
        assert list(row[1]) == embedding
        assert row[2] == 2560

        rel = con.execute(
            "SELECT relation_id, source_entity_id, target_entity_id, chunk_id,"
            " metadata FROM relations WHERE relation_id = ?",
            [relation_id],
        ).fetchone()
        assert rel == (relation_id, entity_id, other_entity, chunk_id, metadata_text)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Analysis-domain round trip (migrations 027-029)
# ---------------------------------------------------------------------------


def test_labeling_rows_round_trip(migrated_dsn: str) -> None:
    """A labeling function and its aggregated chunk label round-trip; the
    migration-028 CHECK mirror is enforced on DuckDB."""
    import duckdb

    tenant = "00000000-0000-0000-0000-000000000001"
    doc_id = "11111111-1111-1111-1111-111111111112"
    chunk_id = "22222222-2222-2222-2222-222222222223"
    lf_id = "55555555-5555-5555-5555-555555555555"
    config = '{"pattern": "^\\\\d{4}"}'
    detail = '{"lf:date-regex": 0.92}'

    con = _connect(migrated_dsn)
    try:
        con.execute("BEGIN TRANSACTION")
        con.execute(
            "INSERT INTO documents (doc_id, tenant_id, source, metadata) VALUES (?, ?, ?, ?)",
            [doc_id, tenant, "labeling-round-trip", "{}"],
        )
        con.execute(
            "INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text,"
            " heading_path, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [chunk_id, tenant, doc_id, 0, "labeled body", "[]", "{}"],
        )
        con.execute(
            "INSERT INTO label_functions (lf_id, tenant_id, name, kind, config)"
            " VALUES (?, ?, ?, ?, ?)",
            [lf_id, tenant, "date-regex", "regex", config],
        )
        con.execute(
            "INSERT INTO chunk_labels (tenant_id, chunk_id, label, probability, detail)"
            " VALUES (?, ?, ?, ?, ?)",
            [tenant, chunk_id, "dated", 0.92, detail],
        )
        con.execute("COMMIT")

        lf = con.execute(
            "SELECT lf_id, kind, config FROM label_functions WHERE lf_id = ?", [lf_id]
        ).fetchone()
        assert lf == (lf_id, "regex", config)

        row = con.execute(
            "SELECT chunk_id, label, probability, detail FROM chunk_labels"
            " WHERE tenant_id = ? AND chunk_id = ?",
            [tenant, chunk_id],
        ).fetchone()
        assert row == (chunk_id, "dated", 0.92, detail)

        # The probability CHECK port is a live constraint, not prose.
        with pytest.raises(duckdb.ConstraintException):
            con.execute(
                "INSERT INTO chunk_labels (tenant_id, chunk_id, label, probability)"
                " VALUES (?, ?, ?, ?)",
                [tenant, "22222222-2222-2222-2222-222222222229", "bad", 1.5],
            )
    finally:
        con.close()


def test_classification_rows_round_trip(migrated_dsn: str) -> None:
    """A label set and its per-chunk prediction round-trip, including the
    nullable calibrated probability and the review flag."""
    import duckdb

    tenant = "00000000-0000-0000-0000-000000000001"
    doc_id = "11111111-1111-1111-1111-111111111113"
    chunk_id = "22222222-2222-2222-2222-222222222224"
    set_id = "66666666-6666-6666-6666-666666666666"
    classes = '["dated", "undated"]'
    params = '{"tier": "setfit", "seed": 42}'

    con = _connect(migrated_dsn)
    try:
        con.execute("BEGIN TRANSACTION")
        con.execute(
            "INSERT INTO documents (doc_id, tenant_id, source, metadata) VALUES (?, ?, ?, ?)",
            [doc_id, tenant, "classification-round-trip", "{}"],
        )
        con.execute(
            "INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text,"
            " heading_path, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [chunk_id, tenant, doc_id, 0, "predicted body", "[]", "{}"],
        )
        con.execute(
            "INSERT INTO label_sets (set_id, tenant_id, name, classes) VALUES (?, ?, ?, ?)",
            [set_id, tenant, "dates", classes],
        )
        con.execute(
            "INSERT INTO chunk_predictions (tenant_id, chunk_id, set_id, label,"
            " probability, calibrated_probability, tier, review, params)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [tenant, chunk_id, set_id, "dated", 0.9, None, "setfit", True, params],
        )
        con.execute("COMMIT")

        row = con.execute(
            "SELECT chunk_id, set_id, label, probability, calibrated_probability,"
            " tier, review, params FROM chunk_predictions"
            " WHERE tenant_id = ? AND chunk_id = ?",
            [tenant, chunk_id],
        ).fetchone()
        assert row == (
            chunk_id,
            set_id,
            "dated",
            0.9,
            None,
            "setfit",
            True,
            params,
        )

        # The nullable calibrated CHECK port rejects out-of-range values.
        with pytest.raises(duckdb.ConstraintException):
            con.execute(
                "INSERT INTO chunk_predictions (tenant_id, chunk_id, set_id, label,"
                " probability, calibrated_probability, tier)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    tenant,
                    "22222222-2222-2222-2222-222222222230",
                    set_id,
                    "undated",
                    0.5,
                    1.5,
                    "head",
                ],
            )
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Schema mirror completeness (seed of the schema-parity manifest)
# ---------------------------------------------------------------------------


def _create_table_names(paths: list[Path]) -> set[str]:
    names: set[str] = set()
    for path in paths:
        # Strip -- comments first: prose can mention DDL spellings.
        sql = re.sub(r"--.*$", "", path.read_text(encoding="utf-8"), flags=re.MULTILINE)
        for match in _CREATE_TABLE_RE.finditer(sql):
            name = match.group(1).split(".")[-1].lower()
            names.add(name)
    return names


def test_every_pg_table_has_a_duckdb_counterpart() -> None:
    pg_tables = _create_table_names(_PG_MIGRATIONS)
    duck_tables = _create_table_names(_DUCKDB_MIGRATIONS)
    missing = pg_tables - duck_tables
    assert not missing, f"tables without a DuckDB mirror: {sorted(missing)}"


def test_base_schema_tables_present(migrated_dsn: str) -> None:
    base = {
        "tenants",
        "documents",
        "chunks",
        "chunks_vectors",
        "entities",
        "relations",
        "projection_checkpoints",
        "projection_dlq",
        "idempotency_keys",
        "tags",
        "document_tags",
        "metadata",
    }
    con = _connect_read_only(migrated_dsn)
    try:
        assert base <= _table_names(con)
    finally:
        con.close()


def test_coding_domain_tables_present(migrated_dsn: str) -> None:
    con = _connect_read_only(migrated_dsn)
    try:
        assert set(_CODING_TABLES) <= _table_names(con)
    finally:
        con.close()
