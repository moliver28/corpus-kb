"""Round-trip tests for the migrate-data legs (issue #127).

Hermetic: the SQLite event-store copy round trip, the FK insert order,
the target helpers, the digest cross-forms (python-parsed vector text vs
native FLOAT[n]; jsonb text vs JSON), and the CLI DSN resolution. Live
(``requires_postgres``, auto-skipped without a server): both directions
over real engines with composed-digest equality across the round trip.

The live legs connect with the SUPERUSER DSN: migration 011 FORCEs row
level security on every tenanted table, so the driver refuses roles
without BYPASSRLS (the one silent-empty-copy shape it can close).
"""

from __future__ import annotations

import uuid
from datetime import UTC
from typing import TYPE_CHECKING

import pytest

from corpus_kb.errors import ConfigError, ConflictError, ValidationError
from corpus_kb.handlers import migrate_roundtrip
from corpus_kb.handlers.migrate_attach import TARGET_NOT_EMPTY, fk_insert_order
from corpus_kb.handlers.migrate_data import ColumnTransform, TablePlan
from corpus_kb.handlers.migrate_digest import (
    _half_quantize,
    compose_digest,
    corpus_digest,
    event_digest,
)
from corpus_kb.handlers.migrate_events import (
    assert_postgres_events_empty,
    collect_sqlite_events,
    originator_hex,
    write_sqlite_events,
)

duckdb = pytest.importorskip("duckdb")

if TYPE_CHECKING:
    import duckdb

DIMS = 4
TENANT = uuid.uuid5(uuid.NAMESPACE_URL, "migrate-rt-tenant")


def _vec_text(seed: float) -> str:
    return "[" + ", ".join(str(seed + i * 0.25) for i in range(DIMS)) + "]"


def _vector_plan_entry() -> TablePlan:
    return TablePlan(
        "main.tv",
        "copy",
        pk=("id",),
        transforms=(ColumnTransform("embedding", DIMS),),
    )


# ---------------------------------------------------------------------------
# Event-store leg (hermetic)
# ---------------------------------------------------------------------------


class TestEventStore:
    @pytest.fixture()
    def seeded_store(self, tmp_path, monkeypatch):
        """A live eventsourcing SQLite store with one aggregate + snapshot."""
        monkeypatch.setenv("CORPUS_KB_SNAPSHOT_PERIOD", "1")
        from corpus_kb.domain.aggregates import Document
        from corpus_kb.domain.application import CorpusApplication, reset_app

        analytics = str(tmp_path / "events-a.duckdb")
        app = CorpusApplication(connection_string=analytics, backend="duckdb")
        try:
            doc = Document(
                tenant_id=TENANT,
                source="roundtrip.py",
                source_type="code",
                file_hash=uuid.uuid4().hex,
            )
            doc.add_chunks(TENANT, chunk_count=2, chunk_texts=["alpha", "beta"])
            app.save(doc)
        finally:
            reset_app()
        # The app derives the sidecar name beside the analytics file — the
        # same convention the copy legs read and write.
        return analytics + ".events.db"

    def test_copy_round_trip_digest_equal(self, tmp_path, seeded_store) -> None:
        events, snapshots = collect_sqlite_events(seeded_store)
        assert len(events) >= 1
        assert len(snapshots) >= 1  # snapshot period 1
        digest = event_digest(events, snapshots)
        assert digest == event_digest(*collect_sqlite_events(seeded_store))

        copy_path = str(tmp_path / "events-b.db")
        write_sqlite_events(copy_path, events, snapshots)
        assert event_digest(*collect_sqlite_events(copy_path)) == digest

    def test_event_digest_changes_when_event_added(self, seeded_store) -> None:
        events, snapshots = collect_sqlite_events(seeded_store)
        assert event_digest(events, snapshots) != event_digest(events[:1], snapshots)

    def test_originator_hex_normalizes_uuid_forms(self) -> None:
        value = uuid.uuid5(uuid.NAMESPACE_URL, "originator")
        dashed = str(value)
        assert originator_hex(value) == value.hex
        assert originator_hex(dashed) == value.hex
        assert originator_hex(dashed.upper()) == value.hex


# ---------------------------------------------------------------------------
# PG event-leg re-run gate (hermetic, over psycopg fakes)
# ---------------------------------------------------------------------------


class _FakeGateCursor:
    """One count query per event table, in call order."""

    def __init__(self, counts: list[int]) -> None:
        self._counts = counts
        self._index = 0
        self.tables: list[str] = []

    def __enter__(self) -> _FakeGateCursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: object = None) -> None:
        self.tables.append(sql.split("FROM ", 1)[1].strip())

    def fetchone(self) -> tuple[int, ...]:
        count = self._counts[self._index]
        self._index += 1
        return (count,)


class _FakeGateConn:
    def __init__(self, counts: list[int]) -> None:
        self._cursor = _FakeGateCursor(counts)

    def __enter__(self) -> _FakeGateConn:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def cursor(self) -> _FakeGateCursor:
        return self._cursor


class _FakeGateRecorder:
    events_table_name = "app_events"


class _FakeGateSnapRecorder:
    snapshot_table_name = "app_snapshots"


class _FakeGateSnapshots:
    recorder = _FakeGateSnapRecorder()


class _FakeGateApp:
    recorder = _FakeGateRecorder()
    snapshots = _FakeGateSnapshots()


def _patch_gate(monkeypatch: pytest.MonkeyPatch, conn: _FakeGateConn) -> None:
    monkeypatch.setattr("psycopg.connect", lambda dsn: conn)
    monkeypatch.setattr(
        "corpus_kb.domain.application.CorpusApplication", lambda conn_str, backend: _FakeGateApp()
    )


class TestPostgresEventGate:
    def test_gate_refuses_a_populated_event_table(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn = _FakeGateConn([2, 0])
        _patch_gate(monkeypatch, conn)
        with pytest.raises(ConflictError, match="already has data"):
            assert_postgres_events_empty("postgresql://u:p@h/corpus_kb")
        assert conn._cursor.tables == ["app_snapshots"]

    def test_gate_passes_an_empty_store_and_checks_both_tables(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conn = _FakeGateConn([0, 0])
        _patch_gate(monkeypatch, conn)
        assert assert_postgres_events_empty("postgresql://u:p@h/corpus_kb") is None
        assert conn._cursor.tables == ["app_snapshots", "app_events"]


# ---------------------------------------------------------------------------
# Target-side helpers (hermetic)
# ---------------------------------------------------------------------------


@pytest.fixture()
def target_con(tmp_path):
    con = duckdb.connect(str(tmp_path / "tgt.duckdb"))
    con.execute("CREATE TABLE main.tenants (tenant_id VARCHAR PRIMARY KEY, name VARCHAR)")
    con.execute(
        "CREATE TABLE main.documents (doc_id UUID PRIMARY KEY, tenant_id VARCHAR,"
        " metadata JSON, FOREIGN KEY (tenant_id) REFERENCES main.tenants(tenant_id))"
    )
    yield con
    con.close()


class TestTargetHelpers:
    def test_target_pks_reads_pk_columns(self, target_con) -> None:
        from corpus_kb.handlers.migrate_attach import target_pks

        assert target_pks(target_con, "main.tenants") == ("tenant_id",)
        assert target_pks(target_con, "main.documents") == ("doc_id",)

    def test_json_columns_detects_metadata(self, target_con) -> None:
        from corpus_kb.handlers.migrate_attach import json_columns

        assert json_columns(target_con, "main.documents") == ("metadata",)
        assert json_columns(target_con, "main.tenants") == ()

    def test_fk_insert_order_puts_referenced_first(self, target_con) -> None:
        order = fk_insert_order(target_con, ["main.documents", "main.tenants"])
        assert order == ("main.tenants", "main.documents")

    def test_fk_insert_order_deterministic_without_edges(self, target_con) -> None:
        order = fk_insert_order(target_con, ["main.tenants"])
        assert order == ("main.tenants",)

    def test_ensure_target_empty(self, target_con) -> None:
        from corpus_kb.handlers.migrate_attach import ensure_target_empty

        ensure_target_empty(target_con, ["main.documents"])
        target_con.execute("INSERT INTO main.tenants VALUES (?, ?)", (str(TENANT), "acme"))
        with pytest.raises(ConflictError) as excinfo:
            ensure_target_empty(target_con, ["main.tenants", "main.documents"])
        assert str(excinfo.value) == TARGET_NOT_EMPTY

    def test_seed_tenant_is_schema_not_data(self, target_con) -> None:
        """The mirror's default-tenant seed does not trip the empty gate,
        and the copy clears it before re-inserting the source's row."""
        from corpus_kb.handlers.migrate_attach import SEED_TENANT_ID, clear_seed_tenant

        target_con.execute("INSERT INTO main.tenants VALUES (?, 'default')", (SEED_TENANT_ID,))
        from corpus_kb.handlers.migrate_attach import ensure_target_empty

        ensure_target_empty(target_con, ["main.tenants"])
        clear_seed_tenant(target_con, "main.tenants")
        assert target_con.execute("SELECT count(*) FROM main.tenants").fetchone() == (0,)

    def test_drop_superseded_hnsw_noop(self, target_con) -> None:
        from corpus_kb.handlers.migrate_attach import drop_superseded_hnsw

        assert drop_superseded_hnsw(target_con) == []


# ---------------------------------------------------------------------------
# Digest cross-forms (hermetic)
# ---------------------------------------------------------------------------


class TestDigestForms:
    def test_vector_text_python_parse_matches_native(self) -> None:
        """The pg-side digest (raw text + Python parse) equals the duckdb
        native-list digest — half quantization makes halfvec symmetric."""
        source = duckdb.connect()
        source.execute("CREATE TABLE tv (id VARCHAR PRIMARY KEY, embedding VARCHAR)")
        source.executemany(
            "INSERT INTO tv VALUES (?, ?)",
            [(f"c-{n}", _vec_text(0.5 + n)) for n in range(3)],
        )
        target = duckdb.connect()
        target.execute("CREATE TABLE tv (id VARCHAR PRIMARY KEY, embedding FLOAT[4])")
        target.executemany(
            "INSERT INTO tv VALUES (?, CAST(? AS FLOAT[4]))",
            [(f"c-{n}", _vec_text(0.5 + n)) for n in range(3)],
        )
        entry = _vector_plan_entry()
        pg_side = corpus_digest(source, [entry], transform_text_vectors=True, python_parse=True)
        duck_side = corpus_digest(target, [entry], transform_text_vectors=False)
        assert pg_side == duck_side

    def test_vector_digest_uses_half_storage(self) -> None:
        """A float that only survives through half storage digests the same
        as its half-rounded form (pgvector halfvec write policy)."""
        raw = 0.10000000149011612  # float32-widened value
        assert _half_quantize([raw]) == _half_quantize(list(_half_quantize([raw])))

    def test_json_transforms_normalize_text_forms(self) -> None:
        source = duckdb.connect()
        source.execute("CREATE TABLE tj (id VARCHAR PRIMARY KEY, payload VARCHAR)")
        source.execute("INSERT INTO tj VALUES ('j-1', '{\"b\": 1, \"a\": [2, 3]}')")
        target = duckdb.connect()
        target.execute("CREATE TABLE tj (id VARCHAR PRIMARY KEY, payload JSON)")
        target.execute("INSERT INTO tj VALUES ('j-1', '{\"a\": [2, 3], \"b\": 1}')")
        entry = TablePlan(
            "main.tj", "copy", pk=("id",), transforms=(ColumnTransform("payload", 0, "json"),)
        )
        assert corpus_digest(source, [entry], transform_text_vectors=True) == corpus_digest(
            target, [entry], transform_text_vectors=True
        )

    def test_digest_is_column_order_independent(self) -> None:
        """Postgres physical column order is ALTER-history order; the
        mirror's is end-state DDL order. Same values must hash equal."""
        left = duckdb.connect()
        left.execute("CREATE TABLE tc (id VARCHAR PRIMARY KEY, a INTEGER, b INTEGER)")
        left.execute("INSERT INTO tc VALUES ('r-1', 1, 2)")
        right = duckdb.connect()
        right.execute("CREATE TABLE tc (id VARCHAR PRIMARY KEY, b INTEGER, a INTEGER)")
        right.execute("INSERT INTO tc VALUES ('r-1', 2, 1)")
        entry = TablePlan("main.tc", "copy", pk=("id",))
        assert corpus_digest(left, [entry], transform_text_vectors=False) == corpus_digest(
            right, [entry], transform_text_vectors=False
        )

    def test_datetime_canonicalizes_to_utc(self) -> None:
        """duckdb reads TIMESTAMPTZ in the local zone, psycopg in UTC:
        the same instant must hash equal."""
        from datetime import datetime, timedelta
        from datetime import timezone as tz

        from corpus_kb.handlers.migrate_digest import _canonical

        local = datetime(2026, 1, 1, 12, 0, 0, tzinfo=tz(timedelta(hours=-8)))
        utc = datetime(2026, 1, 1, 20, 0, 0, tzinfo=UTC)
        assert _canonical(local) == _canonical(utc)

    def test_compose_digest_binds_corpus_and_events(self) -> None:
        assert compose_digest("a", "b") != compose_digest("a", "c")
        assert compose_digest("a", "b") != compose_digest("b", "a")
        assert len(compose_digest("a", "b")) == 64


# ---------------------------------------------------------------------------
# CLI DSN resolution (hermetic)
# ---------------------------------------------------------------------------


class TestResolveDsns:
    def test_duckdb_target_uses_configured_postgres(self) -> None:
        from corpus_kb.cli_migrate import _resolve_dsns

        direction, src, dst = _resolve_dsns(
            "", "duckdb:///C:/data/x.duckdb", "postgresql://u:p@h:5432/corpus_kb"
        )
        assert (direction, src, dst) == (
            "postgres_to_duckdb",
            "postgresql://u:p@h:5432/corpus_kb",
            "duckdb:///C:/data/x.duckdb",
        )

    def test_postgres_target_requires_duckdb_source(self) -> None:
        from corpus_kb.cli_migrate import _resolve_dsns

        with pytest.raises(ConfigError, match="--from"):
            _resolve_dsns("", "postgresql://u:p@h/corpus_kb", "")
        direction, src, dst = _resolve_dsns(
            "duckdb:///C:/data/x.duckdb", "postgresql://u:p@h/corpus_kb", ""
        )
        assert (direction, src, dst) == (
            "duckdb_to_postgres",
            "duckdb:///C:/data/x.duckdb",
            "postgresql://u:p@h/corpus_kb",
        )

    def test_unknown_target_rejected(self) -> None:
        from corpus_kb.cli_migrate import _resolve_dsns

        with pytest.raises(ConfigError, match="--to"):
            _resolve_dsns("", "mysql://nope", "")

    def test_duckdb_source_rejected_for_duckdb_target(self) -> None:
        from corpus_kb.cli_migrate import _resolve_dsns

        with pytest.raises(ConfigError, match="Postgres"):
            _resolve_dsns("duckdb:///C:/src.duckdb", "duckdb:///C:/dst.duckdb", "pg://x")


# ---------------------------------------------------------------------------
# Live legs (requires_postgres, superuser DSN)
# ---------------------------------------------------------------------------


async def _make_db(name: str) -> str:
    """A second scratch database: extensions as superuser, shared bootstrap."""
    import asyncpg

    from corpus_kb._setup.schema_apply import apply_schema
    from tests import research_db

    admin = await asyncpg.connect(research_db.SUPER_DSN)
    await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    await admin.execute(f'CREATE DATABASE "{name}" OWNER {research_db._PG_USER}')
    await admin.close()
    superconn = await asyncpg.connect(research_db.super_dsn_for(name))
    await superconn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    await superconn.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    await superconn.close()
    await apply_schema(f"{research_db.BASE_DSN}/{name}")
    # The leg connects with the SUPERUSER dsn: the BYPASSRLS gate demands
    # it on the target too (FORCE RLS filters reads and checks writes).
    return research_db.super_dsn_for(name)


async def _drop_db(name: str) -> None:
    import asyncpg

    from tests import research_db

    admin = await asyncpg.connect(research_db.SUPER_DSN)
    await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    await admin.close()


@pytest.mark.requires_postgres
class TestLiveRoundTrip:
    @pytest.fixture()
    def isolated_db(self, monkeypatch):
        """A throwaway database name unique to this session: sibling live
        test sessions share the default name and force-drop each other's
        database mid-run (the source of phantom FK failures)."""
        import os

        from tests import research_db

        name = f"corpus_kb_migrt_{os.getpid() % 100000}"
        monkeypatch.setattr(research_db, "DB_NAME", name)
        monkeypatch.setattr(research_db, "DSN", f"{research_db.BASE_DSN}/{name}")
        return name

    async def test_round_trip_digest_equal_both_directions(
        self, tmp_path, monkeypatch, isolated_db
    ) -> None:
        from tests import research_db

        await research_db.create_research_db()
        super_dsn = research_db.super_dsn_for(isolated_db)
        second_name = f"{isolated_db[:47]}_b"
        second_dsn: str | None = None
        try:
            import asyncpg

            from corpus_kb.domain.aggregates import Document
            from corpus_kb.domain.application import CorpusApplication, reset_app

            conn = await asyncpg.connect(super_dsn)
            await conn.execute(
                "INSERT INTO documents (doc_id, tenant_id, source, source_type)"
                " VALUES ($1, $2, 'roundtrip.py', 'code')",
                uuid.uuid4(),
                TENANT,
            )
            await conn.close()

            # Command history with snapshots (period 1 -> one per save).
            monkeypatch.setenv("CORPUS_KB_SNAPSHOT_PERIOD", "1")
            app = CorpusApplication(connection_string=super_dsn, backend="postgres")
            doc = Document(
                tenant_id=TENANT,
                source="roundtrip.py",
                source_type="code",
                file_hash=uuid.uuid4().hex,
            )
            doc.add_chunks(TENANT, chunk_count=1, chunk_texts=["round trip"])
            app.save(doc)
            reset_app()

            # Leg 1: postgres -> DuckDB over the ATTACH scanner.
            duckdb_dsn = f"duckdb:///{(tmp_path / 'rt.duckdb').as_posix()}"
            report1 = await migrate_roundtrip.run_migration(
                "postgres_to_duckdb", super_dsn, duckdb_dsn
            )
            assert report1["events"]["events"] >= 1
            assert report1["events"]["snapshots"] >= 1
            assert any(row["name"] == "documents" and row["rows"] >= 1 for row in report1["tables"])
            assert len(report1["digest"]) == 64

            # The swap needs a clean target: a second run is refused.
            with pytest.raises(ConflictError, match="already has data"):
                await migrate_roundtrip.run_migration("postgres_to_duckdb", super_dsn, duckdb_dsn)

            # Leg 2: DuckDB -> postgres (stream) into a fresh database.
            second_dsn = await _make_db(second_name)
            report2 = await migrate_roundtrip.run_migration(
                "duckdb_to_postgres", duckdb_dsn, second_dsn
            )
            assert report2["digest"] == report1["digest"]
            assert report2["events"]["events"] == report1["events"]["events"]
        finally:
            from corpus_kb.domain.application import reset_app

            reset_app()
            await research_db.drop_research_db()
            await _drop_db(second_name)

    async def test_non_bypass_role_refused(self, isolated_db) -> None:
        """A role FORCE RLS filters reads zero tenanted rows; the driver
        must refuse it before any copy runs. Skips where the scratch role
        itself bypasses (the CI service image)."""
        import asyncpg

        from tests import research_db

        await research_db.create_research_db()
        try:
            conn = await asyncpg.connect(research_db.DSN)
            bypass = await conn.fetchval(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
            await conn.close()
            if bypass:
                pytest.skip("scratch role bypasses RLS (CI service image)")
            with pytest.raises(ValidationError, match="BYPASSRLS"):
                await migrate_roundtrip.run_migration(
                    "postgres_to_duckdb", research_db.DSN, "duckdb:///:memory:"
                )
        finally:
            await research_db.drop_research_db()
