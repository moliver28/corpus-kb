"""Hermetic tests for the eventsourcing backend selection in the domain app.

Covers the DuckDB-port event-store row (program doc section 3.3):
  * env shapes: postgres byte-identical to the pre-port snapshot;
    duckdb -> eventsourcing.sqlite with SQLITE_DBNAME.
  * the sqlite sidecar path helper (duckdb DSN or bare path -> the
    ``<file>.events.db`` store beside the analytics file; :memory: kept).
  * get_app keyed on (backend, conn_str) and close_app releasing the app.
  * a real sqlite round-trip, WAL journal mode, and two-writer saves.
No Postgres is needed anywhere in this file.
"""

from __future__ import annotations

import inspect
import os
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from research_db import TEST_TENANT

from corpus_kb.domain.aggregates import Document
from corpus_kb.errors import ConfigError, error_status

DSN_PG_FULL = "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb"

# Byte-identical snapshot of the env dicts built before the backend split.
PG_ENV_SNAPSHOTS: dict[str, dict[str, str]] = {
    DSN_PG_FULL: {
        "PERSISTENCE_MODULE": "eventsourcing.postgres",
        "POSTGRES_DBNAME": "corpus_kb",
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": "5432",
        "POSTGRES_USER": "corpus_user",
        "POSTGRES_PASSWORD": "corpus_pass",
        "POSTGRES_SCHEMA": "public",
        "CREATE_TABLE": "y",
    },
    "postgresql://localhost/kb": {
        "PERSISTENCE_MODULE": "eventsourcing.postgres",
        "POSTGRES_DBNAME": "kb",
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": "5432",
        "POSTGRES_USER": "corpus_user",
        "POSTGRES_PASSWORD": "",
        "POSTGRES_SCHEMA": "public",
        "CREATE_TABLE": "y",
    },
    "postgresql://u:p@db.example.com:5433/prod": {
        "PERSISTENCE_MODULE": "eventsourcing.postgres",
        "POSTGRES_DBNAME": "prod",
        "POSTGRES_HOST": "db.example.com",
        "POSTGRES_PORT": "5433",
        "POSTGRES_USER": "u",
        "POSTGRES_PASSWORD": "p",
        "POSTGRES_SCHEMA": "public",
        "CREATE_TABLE": "y",
    },
}


@pytest.fixture()
def isolated_apps(monkeypatch):
    """Keep the app cache clean and the env var out of every test."""
    from corpus_kb.domain import application

    monkeypatch.delenv("CORPUS_KB_DATABASE_URL", raising=False)
    application.reset_app()
    yield
    application.reset_app()


# ---------------------------------------------------------------------------
# Env construction
# ---------------------------------------------------------------------------


def test_postgres_env_is_byte_identical_to_snapshot():
    from corpus_kb.domain.application import _eventsourcing_env

    for dsn, expected in PG_ENV_SNAPSHOTS.items():
        assert _eventsourcing_env("postgres", dsn) == expected


def test_duckdb_env_uses_sqlite_module_and_sidecar_path(tmp_path):
    from corpus_kb.domain.application import _eventsourcing_env

    target = tmp_path / "kb.duckdb"
    env = _eventsourcing_env("duckdb", f"duckdb:///{target.as_posix()}")
    assert env == {
        "PERSISTENCE_MODULE": "eventsourcing.sqlite",
        "SQLITE_DBNAME": os.path.normpath(str(target)) + ".events.db",
        "CREATE_TABLE": "y",
    }


def test_unknown_backend_raises_config_error():
    from corpus_kb.domain.application import _eventsourcing_env

    with pytest.raises(ConfigError) as exc:
        _eventsourcing_env("oracle", "oracle://x/y")
    assert error_status(exc.value.error_type) == error_status("config_error")
    assert exc.value.http_status == 400
    assert "postgres" in str(exc.value) and "duckdb" in str(exc.value)


@pytest.mark.parametrize(
    ("conn_str", "expected"),
    [
        ("duckdb:///C:/data/kb.duckdb", os.path.normpath("C:/data/kb.duckdb") + ".events.db"),
        # The gateway DSN convention owns the shape: a 3-slash relative
        # form keeps its leading slash exactly as the analytics file would.
        ("duckdb:///data/kb.duckdb", os.path.normpath("/data/kb.duckdb") + ".events.db"),
        ("C:\\data\\kb.duckdb", os.path.normpath("C:\\data\\kb.duckdb") + ".events.db"),
        ("C:/data/kb.duckdb", os.path.normpath("C:/data/kb.duckdb") + ".events.db"),
        ("kb.duckdb", "kb.duckdb.events.db"),
        ("duckdb:///:memory:", ":memory:"),
    ],
)
def test_sqlite_path_from_conn_str(conn_str, expected):
    """Every input form lands on the sidecar beside the analytics file."""
    from corpus_kb.domain.application import _eventsourcing_env

    env = _eventsourcing_env("duckdb", conn_str)
    assert env["SQLITE_DBNAME"] == expected


def test_get_app_default_backend_is_postgres():
    from corpus_kb.domain.application import get_app

    sig = inspect.signature(get_app)
    assert sig.parameters["connection_string"].default is None
    assert sig.parameters["backend"].default == "postgres"


# ---------------------------------------------------------------------------
# Keyed cache and close_app
# ---------------------------------------------------------------------------


def test_same_key_returns_same_app(tmp_path, isolated_apps):
    from corpus_kb.domain.application import get_app

    dsn = f"duckdb:///{(tmp_path / 'kb.duckdb').as_posix()}"
    assert get_app(dsn, backend="duckdb") is get_app(dsn, backend="duckdb")


def test_different_keys_return_distinct_apps(tmp_path, isolated_apps):
    from corpus_kb.domain.application import get_app

    dsn_a = f"duckdb:///{(tmp_path / 'a.duckdb').as_posix()}"
    dsn_b = f"duckdb:///{(tmp_path / 'b.duckdb').as_posix()}"
    app_a = get_app(dsn_a, backend="duckdb")
    app_b = get_app(dsn_b, backend="duckdb")
    assert app_a is not app_b
    assert app_a.env["SQLITE_DBNAME"] != app_b.env["SQLITE_DBNAME"]


def test_close_app_closes_and_rebuilds_fresh(tmp_path, isolated_apps):
    from corpus_kb.domain.application import close_app, get_app

    dsn = f"duckdb:///{(tmp_path / 'kb.duckdb').as_posix()}"
    key = ("duckdb", dsn)
    app1 = get_app(dsn, backend="duckdb")
    close_app(key)
    assert app1.closing.is_set()
    app2 = get_app(dsn, backend="duckdb")
    assert app2 is not app1
    # The rebuilt app works: save and read one document.
    doc = _make_doc()
    app2.save(doc)
    assert app2.repository.get(doc.id).source == doc.source


def test_close_app_releases_the_sqlite_file(tmp_path, isolated_apps):
    """Windows keeps the file locked while the app holds connections."""
    from corpus_kb.domain.application import close_app, get_app

    db = tmp_path / "kb.duckdb"
    store = tmp_path / "kb.duckdb.events.db"
    dsn = f"duckdb:///{db.as_posix()}"
    get_app(dsn, backend="duckdb")
    close_app(("duckdb", dsn))
    sidecars = [Path(f"{store}-wal"), Path(f"{store}-shm")]
    leftovers = [p for p in [store, *sidecars] if p.exists() and not _unlink(p)]
    assert not leftovers, f"files still locked after close_app: {leftovers}"


def test_close_app_on_missing_key_is_silent(tmp_path, isolated_apps):
    from corpus_kb.domain.application import close_app

    close_app(("duckdb", f"duckdb:///{(tmp_path / 'none.duckdb').as_posix()}"))


# ---------------------------------------------------------------------------
# Real sqlite event store
# ---------------------------------------------------------------------------


def _make_doc() -> Document:
    return Document(
        tenant_id=uuid.UUID(TEST_TENANT),
        source=f"spike://{uuid.uuid4()}",
        source_type="code",
        file_hash="a" * 64,
    )


def test_sqlite_round_trip_across_two_apps(tmp_path, isolated_apps):
    """Save through one app, read through an independent app on one file."""
    from corpus_kb.domain.application import CorpusApplication, get_app

    db = tmp_path / "kb.duckdb"
    app1 = get_app(f"duckdb:///{db.as_posix()}", backend="duckdb")
    app2 = CorpusApplication(db.as_posix(), backend="duckdb")
    assert app1 is not app2

    doc = _make_doc()
    doc.add_chunks(chunk_count=1, chunk_texts=["One chunk."])
    app1.save(doc)

    loaded = app2.repository.get(doc.id)
    assert loaded.source == doc.source
    assert loaded.chunk_count == 1
    assert loaded.version == 2


def test_sqlite_journal_mode_is_wal(tmp_path, isolated_apps):
    from corpus_kb.domain.application import get_app

    db = tmp_path / "kb.duckdb"
    get_app(f"duckdb:///{db.as_posix()}", backend="duckdb")
    with sqlite3.connect(str(db) + ".events.db") as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_two_apps_save_concurrently_without_lock_errors(tmp_path, isolated_apps):
    from corpus_kb.domain.application import CorpusApplication, get_app

    db = tmp_path / "kb.duckdb"
    app1 = get_app(f"duckdb:///{db.as_posix()}", backend="duckdb")
    app2 = CorpusApplication(db.as_posix(), backend="duckdb")

    def save_many(app, tag):
        saved = []
        for _ in range(8):
            doc = _make_doc()
            doc.source = f"spike://{tag}-{uuid.uuid4()}"
            app.save(doc)
            saved.append(doc.id)
        return saved

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(save_many, app1, "a")
        f2 = pool.submit(save_many, app2, "b")
        ids1, ids2 = f1.result(), f2.result()

    assert len(ids1) == 8 and len(ids2) == 8
    for doc_id in [*ids1, *ids2]:
        assert app1.repository.get(doc_id) is not None


def _unlink(path: Path) -> bool:
    try:
        path.unlink()
    except PermissionError:
        return False
    return True
