"""Issue #350 ops on the DuckDB engine: status, export, and event health.

The parity gate proves envelope shape on stubs; this module proves the
REAL reads on a migrated DuckDB file: the status counts and export join
over seeded coding rows, and the event-store health read against the
SQLite sidecar the engine resolves itself (the knowledge plugin 0.6.0
hard-coded). Postgres-side SQL variants stay covered by the parity
legs' stub world plus the schema-parity gate.
"""

from __future__ import annotations

import sqlite3
from typing import Any
from uuid import uuid4

import pytest

from corpus_kb.tools.coding_tools import coding_export, coding_status
from corpus_kb.tools.events_tools import events_summary

TENANT = "00000000-0000-0000-0000-000000000001"


@pytest.fixture()
async def duck_store(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """One migrated DuckDB gateway; the env DSN points at its file."""
    from corpus_kb._setup.migrate import run_duckdb_migrations
    from corpus_kb.storage.gateway import DuckDbGateway

    db_path = tmp_path / "ops.duckdb"
    dsn = f"duckdb:///{db_path.as_posix()}"
    run_duckdb_migrations(dsn)
    monkeypatch.setenv("CORPUS_KB_DATABASE_URL", dsn)
    monkeypatch.delenv("CORPUS_KB_CONFIG", raising=False)
    gateway = await DuckDbGateway.connect(dsn)
    yield gateway
    await gateway.close()


async def _seed_coded_chunk(gateway: Any) -> str:
    """One document + chunk + an accepted final_codes row; returns chunk_id."""
    doc_id = str(uuid4())
    chunk_id = str(uuid4())
    await gateway.execute(
        "INSERT INTO documents (doc_id, tenant_id, source, source_type)"
        " VALUES ($1, $2, 'ops-seed', 'text')",
        doc_id,
        TENANT,
    )
    await gateway.execute(
        "INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text, source_type)"
        " VALUES ($1, $2, $3, 0, 'ops seed text', 'text')",
        chunk_id,
        TENANT,
        doc_id,
    )
    await gateway.execute(
        "INSERT INTO final_codes (chunk_id, code_id, tenant_id, status, route, confidence)"
        " VALUES ($1, 'code-a', $2, 'accepted', 'explicit', 0.9)",
        chunk_id,
        TENANT,
    )
    await gateway.execute(
        "INSERT INTO final_codes (chunk_id, code_id, tenant_id, status, route, confidence)"
        " VALUES ($1, 'code-b', $2, 'rejected', 'residual', 0.1)",
        chunk_id,
        TENANT,
    )
    return chunk_id


async def test_status_and_export_on_duckdb(duck_store: Any) -> None:
    """Status counts the seeded world; export joins text + source."""
    await _seed_coded_chunk(duck_store)

    status = await coding_status(duck_store, tenant_id=TENANT)
    assert status["status"] == "success"
    assert status["documents"] == 1
    assert status["chunks"] == 1
    assert status["coverage"]["accepted"] == 1
    assert status["coverage"]["rejected"] == 1
    assert status["coverage"]["manual_review"] == 0
    assert status["routes"] == {"explicit": 1, "residual": 1}
    # The saturation block is the shared compute_saturation output.
    assert status["saturation"]["unique_codes"] == 1
    assert "isr" in status["saturation"]

    export = await coding_export(duck_store, tenant_id=TENANT)
    assert export["status"] == "success"
    assert export["columns"] == [
        "chunk_id",
        "code_id",
        "confidence",
        "decided_at",
        "text",
        "document_source",
    ]
    assert export["count"] == 1  # only the accepted row exports
    row = export["rows"][0]
    assert row["code_id"] == "code-a"
    assert row["text"] == "ops seed text"
    assert row["document_source"] == "ops-seed"


def _seed_sidecar(db_path: Any, topics: dict[str, int]) -> None:
    """Create the lib-shaped stored_events sidecar with a few topics."""
    path = f"{db_path}.events.db"
    con = sqlite3.connect(path)
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS stored_events ("
            "originator_id TEXT, originator_version INTEGER, topic TEXT,"
            " state BLOB, PRIMARY KEY (originator_id, originator_version))"
        )
        version = 0
        for topic, count in topics.items():
            for _ in range(count):
                version += 1
                con.execute(
                    "INSERT INTO stored_events"
                    " (originator_id, originator_version, topic, state)"
                    " VALUES (?, ?, ?, ?)",
                    (uuid4().hex, version, topic, b"x"),
                )
        con.commit()
    finally:
        con.close()


async def test_events_summary_on_duckdb(duck_store: Any, tmp_path: Any) -> None:
    """The engine resolves the sidecar; counts, checkpoints, DLQ, runs answer."""
    _seed_sidecar(tmp_path / "ops.duckdb", {"corpus_kb.domain.aggregates:Document.Ingested": 2})

    # One checkpoint, one unresolved dead letter, one finished run.
    await duck_store.execute(
        "INSERT INTO projection_checkpoints (projection_name, tenant_id,"
        " last_event_id, last_event_timestamp, checkpoint_timestamp, last_sequence)"
        " VALUES ('documents', $1, 'e-1', NOW(), NOW(), 7)",
        TENANT,
    )
    await duck_store.execute(
        "INSERT INTO projection_dlq (dlq_id, projection_name, tenant_id, event_id,"
        " event_type, error_message) VALUES ('d-1', 'documents', $1, 'e-9',"
        " 'Document.Ingested', 'boom')",
        TENANT,
    )
    await duck_store.execute(
        "INSERT INTO ingest_runs (run_id, tenant_id, source_type, status, error)"
        " VALUES ('r-1', $1, 'text', 'completed', NULL)",
        TENANT,
    )

    summary = await events_summary(duck_store, tenant_id=TENANT)
    assert summary["status"] == "success"
    assert summary["events_store"] == "sqlite"
    assert summary["events_by_topic"] == {"corpus_kb.domain.aggregates:Document.Ingested": 2}
    assert summary["latest_notification_id"] == 2
    assert [c["projection_name"] for c in summary["checkpoints"]] == ["documents"]
    assert summary["checkpoints"][0]["last_sequence"] == 7
    assert summary["dead_letters"]["count"] == 1
    assert summary["dead_letters"]["newest"][0]["error_message"] == "boom"
    assert [r["run_id"] for r in summary["recent_runs"]] == ["r-1"]


async def test_events_summary_absent_store_answers_zero(duck_store: Any) -> None:
    """No sidecar file: honest zeros, never a crash (a fresh database)."""
    summary = await events_summary(duck_store, tenant_id=TENANT)
    assert summary["status"] == "success"
    assert summary["events_store"] == "sqlite"
    assert summary["events_by_topic"] == {}
    assert summary["latest_notification_id"] == 0
    assert summary["dead_letters"]["count"] == 0
    assert summary["recent_runs"] == []


def test_tool_guards_reject_bad_input(duck_store: Any) -> None:
    """A non-object codebook and a bad tenant id raise the typed errors."""
    import asyncio

    from corpus_kb.errors import ValidationError
    from corpus_kb.tools.coding_tools import coding_embed_codebook

    with pytest.raises(ValidationError):
        asyncio.run(coding_embed_codebook(duck_store, "not-an-object", tenant_id=TENANT))
    with pytest.raises(ValidationError):
        asyncio.run(coding_status(duck_store, tenant_id="not-a-uuid"))
