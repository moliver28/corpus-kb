"""DuckDB pins for the research ingest tool entries (event store binding).

``research_ingest_transcript`` and ``research_ingest`` built their handler
without a DSN. The handler then resolved ``CORPUS_KB_DATABASE_URL`` first,
so a set env var sent the event writes to another Postgres. CI has no
Postgres service on two legs, so the writes died there after a 30 s
timeout. The legs pin the config-truth binding: with the env var pointing
at a dead Postgres, the events must still land on the DuckDB file's
SQLite sidecar and the read models must build. Hermetic: no Postgres, no
Ollama.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from corpus_kb._setup.migrate import run_migrations
from corpus_kb.contracts.model import DEFAULT_TENANT_ID
from corpus_kb.storage.duckdb_gateway import DuckDbGateway
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string

DEAD_PG = "postgresql://corpus:corpus@127.0.0.1:59999/deadpg"
TRANSCRIPT = "Moderator: Hello.\nAlice: Hi."


def _bind_config(monkeypatch: pytest.MonkeyPatch, dsn: str) -> None:
    """Bind config truth to the DuckDB file and the env var to a dead PG.

    The env var is the CI shape: it must never win over the pool's store.
    """
    monkeypatch.setattr(
        "corpus_kb.config.load_config",
        lambda: {"database": {"backend": "duckdb", "connection_string": dsn}},
    )
    monkeypatch.setenv("CORPUS_KB_DATABASE_URL", DEAD_PG)


async def _migrated_gateway(tmp_path: Path, name: str) -> tuple[DuckDbGateway, str]:
    path = tmp_path / name
    dsn = duckdb_path_to_conn_string(str(path))
    await run_migrations(dsn)
    gateway = await DuckDbGateway.connect(str(path))
    return gateway, dsn


@pytest.mark.asyncio
async def test_research_ingest_transcript_writes_events_on_duckdb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One transcript file ingests with the event store on the sidecar."""
    from corpus_kb.domain.application import reset_app
    from corpus_kb.handlers.research_handler import reset_research_handler
    from corpus_kb.tools.research_tools import research_ingest_transcript

    gateway, dsn = await _migrated_gateway(tmp_path, "ingest.duckdb")
    _bind_config(monkeypatch, dsn)
    reset_app()
    reset_research_handler()
    transcript = tmp_path / "session.txt"
    transcript.write_text(TRANSCRIPT, encoding="utf-8")
    try:
        result = await research_ingest_transcript(str(transcript), gateway)
        assert result["status"] == "success"
        rows = await gateway.fetch(
            "SELECT doc_id FROM documents WHERE tenant_id = $1 AND source_type = 'transcript'",
            DEFAULT_TENANT_ID,
        )
        assert len(rows) == 1
    finally:
        reset_app()
        reset_research_handler()
        await gateway.close()


@pytest.mark.asyncio
async def test_research_ingest_path_writes_events_on_duckdb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory ingest lands its file with the same event store bound."""
    from corpus_kb.domain.application import reset_app
    from corpus_kb.handlers.research_handler import reset_research_handler
    from corpus_kb.tools.research_tools import research_ingest

    gateway, dsn = await _migrated_gateway(tmp_path, "ingest_dir.duckdb")
    _bind_config(monkeypatch, dsn)
    reset_app()
    reset_research_handler()
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "session.txt").write_text(TRANSCRIPT, encoding="utf-8")
    try:
        result = await research_ingest(str(inbox), gateway)
        assert result["status"] == "success"
        assert result["ingested"] == 1
        assert result["errors"] == 0
        rows = await gateway.fetch(
            "SELECT doc_id FROM documents WHERE tenant_id = $1 AND source_type = 'transcript'",
            DEFAULT_TENANT_ID,
        )
        assert len(rows) == 1
    finally:
        reset_app()
        reset_research_handler()
        await gateway.close()


def test_duck_config_binds_event_store_to_the_sidecar(tmp_path: Path) -> None:
    """A bare-path DuckDB config resolves the event store to the sidecar.

    The runners bind the handler DSN through ``config_conn_str``. A bare
    file path with ``backend: duckdb`` is a supported config shape, so the
    scheme prefix must be added before the store resolves — the raw path
    would open a phantom Postgres store named after the file.
    """
    from corpus_kb.domain.application import close_app, get_app
    from corpus_kb.storage.connect import config_conn_str

    bare = str(tmp_path / "bare.duckdb")
    cfg = {"database": {"backend": "duckdb", "connection_string": bare}}
    conn_str = config_conn_str(cfg)
    assert conn_str.startswith("duckdb:///")
    app = get_app(conn_str)
    try:
        assert str(app.env["PERSISTENCE_MODULE"]) == "eventsourcing.sqlite"
        assert str(app.env["SQLITE_DBNAME"]).endswith("bare.duckdb.events.db")
    finally:
        close_app(("duckdb", conn_str))
