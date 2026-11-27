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
import re
import struct
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from corpus_kb.dimensions import EMBEDDING_DIMENSIONS, RESEARCH_EMBEDDING_DIMENSIONS
from corpus_kb.errors import ConflictError, ValidationError
from corpus_kb.handlers import migrate_data
from corpus_kb.handlers.migrate_digest import _canonical_json, _half_quantize

duckdb = pytest.importorskip("duckdb")

if TYPE_CHECKING:
    import duckdb

BANK_MISMATCH = (
    "The copied data did not match the source. Fix the cause, then run migrate-data again."
)

DIMS = 2560

# Ordered report keys (zero-drift rule 9): deterministic key order.
REPORT_KEYS = ["direction", "tables", "rows", "elapsed_seconds", "digest", "skipped"]


def _expected_dims(spec: str) -> int:
    """A mirror DDL width spec to its Python width number."""
    if spec.startswith("__"):
        widths = {
            "__EMBEDDING_DIMS__": EMBEDDING_DIMENSIONS,
            "__RESEARCH_EMBEDDING_DIMS__": RESEARCH_EMBEDDING_DIMENSIONS,
        }
        if spec not in widths:
            raise AssertionError(f"unknown width placeholder {spec}")
        return widths[spec]
    return int(spec)


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
    def test_discovery_skips_duckdb_runtime_artifacts(
        self, source: duckdb.DuckDBPyConnection
    ) -> None:
        """Issue #528 failure 1: a corpus that served one search carries
        fts_main_<table> schemas; DuckPGQ and the rag backend add more
        runtime state. None of it enters the copy plan."""
        source.execute("CREATE SCHEMA fts_main_chunks")
        source.execute("CREATE TABLE fts_main_chunks.dict (rid VARCHAR, term VARCHAR)")
        source.execute("CREATE SCHEMA __duckpgq_internal")
        source.execute("CREATE TABLE __duckpgq_internal.proptables (id INTEGER)")
        source.execute("CREATE TABLE rag_nodes (node_id VARCHAR, payload VARCHAR)")
        bares = {name.split(".")[-1] for name in migrate_data.discover_tables(source)}
        assert "dict" not in bares
        assert "proptables" not in bares
        assert "rag_nodes" not in bares
        assert "tenants" in bares

    def test_plan_attaches_transforms_to_every_mirror_vector_column(self) -> None:
        """Issue #528 failure 2 class-gap: every FLOAT[n] column in the
        DuckDB mirror migrations has a transform, so a populated research
        or shadow table can never kill the postgres leg again."""
        dir_path = Path(migrate_data.__file__).parent.parent / "migrations_duckdb"
        mirror: dict[str, dict[str, int]] = {}
        for path in sorted(dir_path.glob("*.sql")):
            if path.name.startswith("rollback_"):
                continue
            in_table = ""
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.split("--")[0].strip()
                if head := re.fullmatch(r"CREATE TABLE (?:IF NOT EXISTS )?(\w+) \(", stripped):
                    in_table = head.group(1)
                elif head := re.fullmatch(
                    r"ALTER TABLE (\w+) ADD COLUMN (?:IF NOT EXISTS )?(\w+) FLOAT\[(\d+|__\w+__)\]",
                    stripped,
                ):
                    mirror.setdefault(head.group(1), {})[head.group(2)] = head.group(3)
                elif in_table and (col := re.match(r"(\w+)\s+FLOAT\[(\d+|__\w+__)\]", stripped)):
                    mirror.setdefault(in_table, {})[col.group(1)] = col.group(2)
                elif stripped.startswith(")"):
                    in_table = ""
        assert mirror, "parsed no vector columns from the mirror DDL"
        for table, columns in mirror.items():
            transforms = {
                t.column: t.dims for t in migrate_data.VECTOR_TEXT_TRANSFORMS.get(table, ())
            }
            for column, dims in columns.items():
                assert transforms.get(column) == _expected_dims(dims), (
                    f"{table}.{column} FLOAT[{dims}] has no text transform"
                )

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


class TestDialectOnlyColumns:
    def test_copy_and_digest_drop_dialect_only_columns(
        self, source: duckdb.DuckDBPyConnection, target: duckdb.DuckDBPyConnection
    ) -> None:
        """Third repro blocker (issue #528): ingest_runs.source is
        DuckDB-only (migration duckdb-030). The copy selects the shared
        columns and both digest sides hash the same column set."""
        source.execute(
            "CREATE TABLE ingest_runs (run_id VARCHAR PRIMARY KEY,"
            " tenant_id VARCHAR, source VARCHAR)"
        )
        target.execute("CREATE TABLE ingest_runs (run_id VARCHAR PRIMARY KEY, tenant_id VARCHAR)")
        source.executemany(
            "INSERT INTO ingest_runs VALUES (?, ?, ?)",
            [("r-1", "t-1", "https://x"), ("r-2", "t-2", None)],
        )
        entry = {
            e.name.split(".")[-1]: e
            for e in migrate_data.build_plan(migrate_data.discover_tables(source))
        }["ingest_runs"]
        assert entry.transforms == ()
        copied = migrate_data._copy_table(source, target, entry)
        assert copied == 2
        target_rows = target.execute(
            "SELECT run_id, tenant_id FROM ingest_runs ORDER BY run_id"
        ).fetchall()
        assert target_rows == [("r-1", "t-1"), ("r-2", "t-2")]
        src = migrate_data._corpus_digest(source, [entry], transform_text_vectors=True)
        dst = migrate_data._corpus_digest(target, [entry], transform_text_vectors=False)
        assert src == dst


class TestDigestCanonicalization:
    def test_canonical_json_handles_pg_dict_form(self) -> None:
        """Fourth repro blocker (issue #528): psycopg returns postgres
        jsonb as a Python object; the duckdb side returns JSON text.
        Both forms canonicalize identically instead of crashing."""
        value = {"title": "Alpha", "n": 1}
        assert _canonical_json(value) == _canonical_json(json.dumps(value))

    def test_half_quantize_equal_across_engine_forms(self) -> None:
        """Fifth repro blocker (issue #528): DuckDB FLOAT[n] returns exact
        float32 doubles and pgvector text carries shortest float4 decimals.
        A float32 exactly halfway between two halves rounds to different
        halves without the float32 pre-pass; both engine forms must
        quantize equal."""
        midpoint = struct.unpack("f", struct.pack("f", 2.0**-14 + 2.0**-25))[0]
        duck_form = midpoint
        pg_form = None
        for precision in range(1, 10):
            text = f"{midpoint:.{precision}g}"
            if struct.pack("f", float(text)) == struct.pack("f", midpoint):
                pg_form = float(text)
                break
        assert pg_form is not None and pg_form != midpoint
        assert _half_quantize([duck_form]) == _half_quantize([pg_form])


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
        with pytest.raises(ValidationError, match="direction"):
            migrate_data.migrate(source, target, "sideways")
