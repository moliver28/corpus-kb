"""`corpus-kb setup --backend duckdb`: the no-Docker one-command path.

Hermetic: real duckdb migrations + eventsourcing sidecar on tmp paths;
the network-touching extension install is patched.
"""

from __future__ import annotations

import sqlite3
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

import duckdb
import pytest
import yaml

from corpus_kb._setup.setup_duckdb import (
    DEFAULT_DUCKDB_FILE,
    preinstall_extensions,
    provision_sidecar,
    resolve_duckdb_dsn,
    setup_duckdb_cmd,
)
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string


@pytest.fixture(autouse=True)
def _offline_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """No network in tests: the extension pre-install is a recorded stub."""

    def _fake() -> int:
        return 0

    monkeypatch.setattr("corpus_kb._setup.setup_duckdb.preinstall_extensions", _fake)


# --- DSN resolution ---------------------------------------------------------------


def test_resolve_dsn_explicit_bare_path_becomes_dsn(tmp_path: Path) -> None:
    resolved = resolve_duckdb_dsn({}, str(tmp_path / "my.duckdb"))
    assert resolved == duckdb_path_to_conn_string(str(tmp_path / "my.duckdb"))


def test_resolve_dsn_relative_path_resolves_absolute(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A relative --dsn must not strand the sidecar at the drive root."""
    monkeypatch.chdir(tmp_path)
    resolved = resolve_duckdb_dsn({}, "kb.duckdb")
    assert resolved == duckdb_path_to_conn_string(str(tmp_path / "kb.duckdb"))
    from corpus_kb.storage.duckdb_support import events_sidecar_path

    assert events_sidecar_path(resolved) == str(tmp_path / "kb.duckdb.events.db")


def test_resolve_dsn_explicit_dsn_wins(tmp_path: Path) -> None:
    dsn = duckdb_path_to_conn_string(str(tmp_path / "a.duckdb"))
    assert resolve_duckdb_dsn({}, dsn) == dsn


def test_resolve_dsn_from_config() -> None:
    config = {"database": {"connection_string": "duckdb:///C:/data/kb.duckdb"}}
    assert resolve_duckdb_dsn(config, "") == "duckdb:///C:/data/kb.duckdb"


def test_resolve_dsn_default_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    assert resolve_duckdb_dsn({}, "").endswith(DEFAULT_DUCKDB_FILE)


# --- dry run -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_prints_steps_and_creates_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    out = StringIO()
    with patch("sys.stdout", out):
        rc = await setup_duckdb_cmd({}, dry_run=True, dsn_arg="")

    assert rc == 0
    text = out.getvalue()
    assert DEFAULT_DUCKDB_FILE in text
    assert ".events.db" in text
    assert "config.yaml" in text
    assert "dry run" in text.lower()
    assert not (tmp_path / DEFAULT_DUCKDB_FILE).exists()
    assert not (tmp_path / "config.yaml").exists()


# --- apply path --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_provisions_files_and_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "kb.duckdb"
    out = StringIO()
    with (
        patch("sys.stdout", out),
        patch("corpus_kb._setup.install.confirm", return_value=True),
    ):
        rc = await setup_duckdb_cmd({}, dry_run=False, dsn_arg=str(db))

    assert rc == 0, out.getvalue()
    # analytics file: migrations applied (schema_migrations tracking table)
    con = duckdb.connect(str(db), read_only=True)
    applied = con.execute("SELECT count(*) FROM schema_migrations").fetchone()[0]
    con.close()
    assert applied > 0
    # sidecar: eventsourcing schema present
    with sqlite3.connect(str(db) + ".events.db") as side:
        tables = {r[0] for r in side.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "stored_events" in tables
    # config pair written
    data: dict[str, Any] = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert data["database"]["backend"] == "duckdb"
    assert data["database"]["connection_string"] == duckdb_path_to_conn_string(str(db))


@pytest.mark.asyncio
async def test_apply_is_idempotent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "kb.duckdb"
    argv: dict[str, Any] = {"dry_run": False, "dsn_arg": str(db)}
    for _ in range(2):
        with (
            patch("sys.stdout", StringIO()),
            patch("corpus_kb._setup.install.confirm", return_value=True),
        ):
            rc = await setup_duckdb_cmd({}, **argv)
        assert rc == 0

    con = duckdb.connect(str(db), read_only=True)
    applied = con.execute("SELECT count(*) FROM schema_migrations").fetchone()[0]
    con.close()
    with sqlite3.connect(str(db) + ".events.db") as side:
        events = side.execute("SELECT count(*) FROM stored_events").fetchone()[0]
    assert applied > 0
    assert events == 0


@pytest.mark.asyncio
async def test_declined_confirm_mutates_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    out = StringIO()
    with (
        patch("sys.stdout", out),
        patch("corpus_kb._setup.install.confirm", return_value=False),
    ):
        rc = await setup_duckdb_cmd({}, dry_run=False, dsn_arg=str(tmp_path / "kb.duckdb"))

    assert rc == 0
    assert not (tmp_path / "kb.duckdb").exists()
    assert not (tmp_path / "config.yaml").exists()


@pytest.mark.asyncio
async def test_failed_provisioning_leaves_config_byte_identical(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed step must never overwrite a working config file.

    A Postgres deployment points ``database:`` at a working server. When a
    provisioning step fails (say the migrations), the run must end without
    flipping that block to a DSN at an unmigrated file.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CORPUS_KB_CONFIG", raising=False)
    config = tmp_path / "config.yaml"
    original = (
        b"database:\n  backend: postgres\n  connection_string: postgresql://prod:5432/corpus_kb\n"
    )
    config.write_bytes(original)
    db = tmp_path / "kb.duckdb"

    async def broken_analytics(_dsn: str) -> int:
        return 1

    monkeypatch.setattr("corpus_kb._setup.setup_duckdb.provision_analytics", broken_analytics)
    out = StringIO()
    with (
        patch("sys.stdout", out),
        patch("corpus_kb._setup.install.confirm", return_value=True),
    ):
        rc = await setup_duckdb_cmd({}, dry_run=False, dsn_arg=str(db))

    assert rc == 1
    assert config.read_bytes() == original, "the config must be untouched"
    assert "config was not changed" in out.getvalue()


# --- wiring (install.setup_cmd + argparse) -----------------------------------------


@pytest.mark.asyncio
async def test_install_setup_cmd_routes_duckdb(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """install.setup_cmd(backend='duckdb') reaches the duckdb plan, no psutil needed."""
    monkeypatch.chdir(tmp_path)
    from corpus_kb._setup import install

    out = StringIO()
    with patch("sys.stdout", out):
        rc = await install.setup_cmd({}, dry_run=True, fresh=False, backend="duckdb")
    assert rc == 0
    assert "DuckDB Setup" in out.getvalue()


@pytest.mark.asyncio
async def test_install_setup_cmd_rejects_unknown_backend() -> None:
    from corpus_kb._setup import install

    out = StringIO()
    with patch("sys.stdout", out):
        rc = await install.setup_cmd({}, dry_run=True, fresh=False, backend="sqlite")
    assert rc == 2
    assert "unknown backend" in out.getvalue()


def test_main_parses_backend_and_dsn_flags(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    from corpus_kb._setup import install

    out = StringIO()
    with patch("sys.stdout", out):
        rc = install.main(["setup", "--backend", "duckdb", "--dry-run"])
    assert rc == 0
    assert "DuckDB Setup" in out.getvalue()
    assert DEFAULT_DUCKDB_FILE in out.getvalue()


def test_provision_sidecar_creates_schema(tmp_path: Path) -> None:
    db = tmp_path / "kb.duckdb"
    provision_sidecar(duckdb_path_to_conn_string(str(db)))
    sidecar = tmp_path / "kb.duckdb.events.db"
    assert sidecar.exists()
    with sqlite3.connect(str(sidecar)) as side:
        tables = {r[0] for r in side.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "stored_events" in tables


def test_provision_sidecar_rejects_sidecar_dsn(tmp_path: Path) -> None:
    out = StringIO()
    with patch("sys.stdout", out):
        rc = provision_sidecar(duckdb_path_to_conn_string(str(tmp_path / "kb.duckdb.events.db")))
    assert rc == 1


def _fake_connect(recorder: list[str], fail: set[str]) -> Any:
    class _Con:
        def execute(self, sql: str) -> _Con:
            recorder.append(sql)
            for name in fail:
                if name in sql:
                    raise RuntimeError(f"cannot download {name}")
            return self

        def close(self) -> None:
            return None

    return lambda _path: _Con()


def test_preinstall_extensions_installs_fts(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(duckdb, "connect", _fake_connect(calls, set()))
    assert preinstall_extensions() == 0
    assert any("INSTALL fts" in c for c in calls)


def test_preinstall_extensions_offline_note_keeps_going(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """vss failure never fails setup; fts failure prints the vendoring fix."""
    calls: list[str] = []
    monkeypatch.setattr(duckdb, "connect", _fake_connect(calls, fail={"INSTALL"}))
    out = StringIO()
    with patch("sys.stdout", out):
        rc = preinstall_extensions()
    assert rc == 1
    text = out.getvalue()
    assert "extension_directory" in text
