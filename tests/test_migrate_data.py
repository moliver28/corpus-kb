"""Hermetic duckdb-to-duckdb tests for handlers.migrate_data.

No migrations runner is required: the stand-in schema below is the minimal
three-table mirror (tenants, documents, chunks_vectors). When
migrations_duckdb lands (sp-duckdb-migrations), a follow-up test re-runs
migrate() over the real mirrored schema — tracked as the integration
follow-up of issue #127 WP sp-migrate-data.

The direction label denotes the migration leg (source side -> target
side); the duckdb files stand in for the real engines, so the tests run
the "postgres_to_duckdb" path hermetically.

Pinned behaviors: stream copy preserves rows (FLOAT[2560], JSON text,
uuid strings), the vector-text re-parse turns '[0.1, 0.2]' into FLOAT[n],
the corpus digest is stable on identical data and changes when a row
changes (ConflictError with the bank string), count mismatches raise, and
the plan covers every manifest table exactly once.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from corpus_kb.errors import ConflictError
from corpus_kb.handlers import migrate_data

duckdb = pytest.importorskip("duckdb")

if TYPE_CHECKING:
    import duckdb

BANK_MISMATCH = (
    "The copied data did not match the source. Fix the cause, then run migrate-data again."
)

DIMS = 2560

# Ordered report keys (zero-drift rule 9): deterministic key order.
REPORT_KEYS = ["direction", "tables", "rows", "elapsed_seconds", "digest", "skipped"]


def _vector_text(seed: float) -> str:
    return "[" + ", ".join(str(seed + i * 0.001) for i in range(DIMS)) + "]"


def _copy_plan(con: duckdb.DuckDBPyConnection) -> list:
    plan = migrate_data.build_plan(migrate_data.discover_tables(con))
    return [entry for entry in plan if entry.disposition == "copy"]


@pytest.fixture()
def source(tmp_path: pytest.TempPathFactory) -> duckdb.DuckDBPyConnection:
    """Source file: chunks_vectors.embedding is VARCHAR vector text, the
    ATTACH-leg shape the W0 probe observed over the postgres scanner."""
    con = duckdb.connect(str(tmp_path / "src.duckdb"))
    con.execute("CREATE TABLE tenants (tenant_id VARCHAR PRIMARY KEY, name VARCHAR)")
    con.execute(
        "CREATE TABLE documents (doc_id VARCHAR PRIMARY KEY, tenant_id VARCHAR, metadata JSON)"
    )
    con.execute(
        "CREATE TABLE chunks_vectors (chunk_id VARCHAR PRIMARY KEY,"
        " tenant_id VARCHAR, embedding VARCHAR)"
    )
    con.executemany(
        "INSERT INTO tenants VALUES (?, ?)",
        [("t-1", "acme"), ("t-2", "beta")],
    )
    con.executemany(
        "INSERT INTO documents VALUES (?, ?, ?)",
        [
            ("d-1", "t-1", json.dumps({"title": "Alpha", "n": 1})),
            ("d-2", "t-2", json.dumps({"title": "Beta"})),
        ],
    )
    con.executemany(
        "INSERT INTO chunks_vectors VALUES (?, ?, ?)",
        [("c-1", "t-1", _vector_text(0.1)), ("c-2", "t-2", _vector_text(0.5))],
    )
    yield con
    con.close()


@pytest.fixture()
def target(tmp_path: pytest.TempPathFactory) -> duckdb.DuckDBPyConnection:
    """Target file: native FLOAT[2560] embedding, the mirrored regime."""
    con = duckdb.connect(str(tmp_path / "dst.duckdb"))
    con.execute("CREATE TABLE tenants (tenant_id VARCHAR PRIMARY KEY, name VARCHAR)")
    con.execute(
        "CREATE TABLE documents (doc_id VARCHAR PRIMARY KEY, tenant_id VARCHAR, metadata JSON)"
    )
    con.execute(
        "CREATE TABLE chunks_vectors (chunk_id VARCHAR PRIMARY KEY,"
        " tenant_id VARCHAR, embedding FLOAT[2560])"
    )
    yield con
    con.close()


class TestPlan:
    def test_manifest_tables_unique(self) -> None:
        bares = [entry.name.split(".")[-1] for entry in migrate_data.EXEMPTION_MANIFEST]
        assert len(bares) == len(set(bares)), "manifest has duplicate tables"

    def test_plan_covers_source_tables_once(self, source: duckdb.DuckDBPyConnection) -> None:
        plan = migrate_data.build_plan(migrate_data.discover_tables(source))
        names = [entry.name for entry in plan]
        assert len(names) == len(set(names)), "plan has duplicate tables"
        by_bare = {entry.name.split(".")[-1]: entry for entry in plan}
        assert by_bare["tenants"].disposition == "copy"
        assert by_bare["documents"].disposition == "copy"
        assert by_bare["chunks_vectors"].disposition == "copy"

    def test_plan_attaches_vector_transform(self, source: duckdb.DuckDBPyConnection) -> None:
        by_bare = {
            entry.name.split(".")[-1]: entry
            for entry in migrate_data.build_plan(migrate_data.discover_tables(source))
        }
        entry = by_bare["chunks_vectors"]
        assert entry.transforms[0].column == "embedding"
        assert entry.transforms[0].dims == DIMS


class TestCopy:
    def test_copy_preserves_rows_and_reparses_vectors(
        self, source: duckdb.DuckDBPyConnection, target: duckdb.DuckDBPyConnection
    ) -> None:
        report = migrate_data.migrate(source, target, "postgres_to_duckdb")
        vectors = target.execute(
            "SELECT chunk_id, embedding FROM chunks_vectors ORDER BY chunk_id"
        ).fetchall()
        assert vectors[0][0] == "c-1"
        assert len(vectors[0][1]) == DIMS
        assert vectors[0][1][0] == pytest.approx(0.1, abs=1e-6)
        # JSON text and uuid strings survive byte-stable.
        docs = target.execute("SELECT doc_id, metadata FROM documents ORDER BY doc_id").fetchall()
        assert docs[0][0] == "d-1"
        assert json.loads(str(docs[0][1]))["title"] == "Alpha"
        tenants = target.execute("SELECT count(*) FROM tenants").fetchone()
        assert tenants == (2,)
        assert report["rows"] == 6
        assert list(report) == REPORT_KEYS

    def test_reparse_hook_handles_spaced_text(
        self, source: duckdb.DuckDBPyConnection, target: duckdb.DuckDBPyConnection
    ) -> None:
        entry = next(
            e
            for e in migrate_data.build_plan(migrate_data.discover_tables(source))
            if e.name.split(".")[-1] == "chunks_vectors"
        )
        migrate_data._copy_table(source, target, entry)
        got = target.execute(
            "SELECT embedding FROM chunks_vectors WHERE chunk_id = 'c-1'"
        ).fetchone()
        assert got is not None
        vec = got[0]
        assert vec[0] == pytest.approx(0.1, abs=1e-6)
        assert vec[1] == pytest.approx(0.101, abs=1e-6)

    def test_null_vector_tolerated(
        self, source: duckdb.DuckDBPyConnection, target: duckdb.DuckDBPyConnection
    ) -> None:
        source.execute("INSERT INTO chunks_vectors VALUES ('c-3', 't-1', NULL)")
        migrate_data.migrate(source, target, "postgres_to_duckdb")
        got = target.execute(
            "SELECT embedding FROM chunks_vectors WHERE chunk_id = 'c-3'"
        ).fetchone()
        assert got is not None and got[0] is None


class TestDigest:
    def test_digest_stable_across_runs(self, source: duckdb.DuckDBPyConnection) -> None:
        plan = _copy_plan(source)
        first = migrate_data._corpus_digest(source, plan, transform_text_vectors=True)
        second = migrate_data._corpus_digest(source, plan, transform_text_vectors=True)
        assert first == second
        assert len(first) == 64

    def test_digest_changes_when_row_changes(self, source: duckdb.DuckDBPyConnection) -> None:
        plan = _copy_plan(source)
        before = migrate_data._corpus_digest(source, plan, transform_text_vectors=True)
        source.execute(
            "UPDATE documents SET metadata = ? WHERE doc_id = 'd-1'",
            [json.dumps({"n": 2})],
        )
        after = migrate_data._corpus_digest(source, plan, transform_text_vectors=True)
        assert before != after

    def test_digest_matches_across_copies(
        self, source: duckdb.DuckDBPyConnection, target: duckdb.DuckDBPyConnection
    ) -> None:
        migrate_data.migrate(source, target, "postgres_to_duckdb")
        plan = _copy_plan(source)
        src = migrate_data._corpus_digest(source, plan, transform_text_vectors=True)
        dst = migrate_data._corpus_digest(target, plan, transform_text_vectors=False)
        assert src == dst


class TestVerify:
    def test_count_mismatch_raises_bank_string(
        self, source: duckdb.DuckDBPyConnection, target: duckdb.DuckDBPyConnection
    ) -> None:
        migrate_data.migrate(source, target, "postgres_to_duckdb")
        target.execute("DELETE FROM documents WHERE doc_id = 'd-2'")
        with pytest.raises(ConflictError) as excinfo:
            migrate_data._verify_counts(source, target, _copy_plan(source))
        assert str(excinfo.value) == BANK_MISMATCH

    def test_migrate_raises_when_copy_drops_rows(
        self,
        source: duckdb.DuckDBPyConnection,
        target: duckdb.DuckDBPyConnection,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A silent partial copy (stream cut, ATTACH failure) must surface as
        ConflictError with the bank string, never as a 'successful' swap."""

        def _drop(source_conn: object, target_conn: object, entry: object) -> int:
            return 0

        # duckdb_swap (the migrate() body, split to migrate_roundtrip) reads
        # _copy_table from its own module namespace.
        from corpus_kb.handlers import migrate_roundtrip

        monkeypatch.setattr(migrate_roundtrip, "_copy_table", _drop)
        with pytest.raises(ConflictError) as excinfo:
            migrate_data.migrate(source, target, "postgres_to_duckdb")
        assert str(excinfo.value) == BANK_MISMATCH

    def test_target_mutation_breaks_digest_match(
        self, source: duckdb.DuckDBPyConnection, target: duckdb.DuckDBPyConnection
    ) -> None:
        migrate_data.migrate(source, target, "postgres_to_duckdb")
        target.execute(
            "UPDATE chunks_vectors SET embedding = ? WHERE chunk_id = 'c-1'",
            [[0.9] * DIMS],
        )
        plan = _copy_plan(source)
        src = migrate_data._corpus_digest(source, plan, transform_text_vectors=True)
        dst = migrate_data._corpus_digest(target, plan, transform_text_vectors=False)
        assert src != dst


class TestReport:
    def test_report_keys_deterministic_order(
        self, source: duckdb.DuckDBPyConnection, target: duckdb.DuckDBPyConnection
    ) -> None:
        report = migrate_data.migrate(source, target, "postgres_to_duckdb")
        assert list(report) == REPORT_KEYS
        names = [table["name"] for table in report["tables"]]
        assert names == sorted(names)
        assert report["skipped"] == []

    def test_invalid_direction_rejected(
        self, source: duckdb.DuckDBPyConnection, target: duckdb.DuckDBPyConnection
    ) -> None:
        with pytest.raises(ValueError, match="direction"):
            migrate_data.migrate(source, target, "sideways")
