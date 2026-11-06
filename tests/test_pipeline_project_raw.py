"""Track C3: the pipeline's project plumbing on a real DuckDB backend.

Covers the done-when list: a provisioned project run stores raw
content-addressed rows, stamps ``documents.project_id`` and
``ingest_runs.project_id``, and reports ``raw_table`` / ``raw_stored``;
the watch-suffix table carries the new modalities; the queue worker
passes the row's project id through.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("duckdb")

from corpus_kb._setup.migrate import run_migrations
from corpus_kb.extraction import ExtractStats
from corpus_kb.ingest_watch import WATCH_SUFFIXES
from corpus_kb.queue.worker import row_project_id
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string
from corpus_kb.storage.gateway import DuckDbGateway
from corpus_kb.storage.project_tables import create_project_table
from corpus_kb.tools import ingest_common
from corpus_kb.tools.ingest_common import run_pipeline
from corpus_kb.utils.models import Chunk, Entity, Relation

pytestmark = pytest.mark.asyncio

TENANT = "00000000-0000-0000-0000-000000000001"
PROJECT = "00000000-0000-0000-0000-000000000002"
DOC_PATH = "project-doc.txt"
DOC_TEXT = "Project raw-content test body. " * 20


def _pipeline_config() -> dict[str, Any]:
    return {
        "database": {"backend": "duckdb"},
        "embedding": {"model": "stub-embed", "dimensions": 2560},
        "graph": {"extract_entities": True},
        "ingest": {"contextualize": {"enabled": False}},
    }


@pytest.fixture
async def duckdb_gateway(tmp_path: Path):
    """A migrated tmp .duckdb file behind one DuckDbGateway."""
    path = tmp_path / "project-raw.duckdb"
    await run_migrations(duckdb_path_to_conn_string(str(path)))
    gateway = await DuckDbGateway.connect(str(path))
    try:
        yield gateway
    finally:
        await gateway.close()
        for suffix in ("", ".wal"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def _stub_stages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic embed/extract doubles: no Ollama, no network."""
    from corpus_kb.dimensions import EMBEDDING_DIMENSIONS

    async def fake_embed(chunks: list[Chunk], config: dict[str, Any], **kwargs: Any):
        for chunk in chunks:
            chunk.embedding = [0.001] * EMBEDDING_DIMENSIONS
        return False, None

    async def fake_extract(
        chunks: list[Chunk],
        ontology: Any,
        source_document_id: str,
        config: dict[str, Any],
        pool: Any = None,
    ) -> tuple[list[Entity], list[Relation], str, ExtractStats]:
        return [], [], "stub", ExtractStats(backend="stub")

    monkeypatch.setattr(ingest_common, "embed_chunks", fake_embed)
    monkeypatch.setattr(ingest_common, "extract_with_fallback", fake_extract)


async def test_ingest_with_project_stamps_and_raw_row(duckdb_gateway: DuckDbGateway) -> None:
    gw = duckdb_gateway
    await create_project_table(gw, TENANT, PROJECT, "meeting-notes")

    result = await run_pipeline(
        DOC_TEXT, "text", DOC_PATH, _pipeline_config(), gw, TENANT, project_id=PROJECT
    )

    assert result["status"] == "success"
    assert result["raw_table"] == "raw_meeting_notes"
    stored = result["raw_stored"]
    assert stored["text"] == len(DOC_TEXT)
    assert len(stored["sha256"]) == 64

    row = await gw.fetchrow("SELECT raw_text, project_id, tenant_id FROM raw_meeting_notes")
    assert row is not None
    assert row["raw_text"] == DOC_TEXT
    assert str(row["project_id"]) == PROJECT

    doc = await gw.fetchrow(
        "SELECT project_id FROM documents WHERE doc_id::text = $1", result["document_id"]
    )
    assert doc is not None and str(doc["project_id"]) == PROJECT

    run_row = await gw.fetchrow(
        "SELECT project_id FROM ingest_runs WHERE run_id::text = $1", result["run_id"]
    )
    assert run_row is not None and str(run_row["project_id"]) == PROJECT


async def test_ingest_without_project_writes_no_raw_row(duckdb_gateway: DuckDbGateway) -> None:
    result = await run_pipeline(
        DOC_TEXT, "text", DOC_PATH, _pipeline_config(), duckdb_gateway, TENANT
    )
    assert result["status"] == "success"
    assert "raw_table" not in result
    tables = await duckdb_gateway.fetchval("SELECT COUNT(*) FROM project_tables")
    assert int(tables or 0) == 0


async def test_ingest_without_provisioned_table_writes_no_raw_row(
    duckdb_gateway: DuckDbGateway,
) -> None:
    """A project_id with no registry row stays honest: no table, no keys."""
    result = await run_pipeline(
        DOC_TEXT,
        "text",
        DOC_PATH,
        _pipeline_config(),
        duckdb_gateway,
        TENANT,
        project_id=PROJECT,
    )
    assert result["status"] == "success"
    assert "raw_table" not in result


async def test_watch_suffixes_carry_the_project_modalities() -> None:
    for suffix in (".pdf", ".docx", ".vtt", ".srt"):
        assert suffix in WATCH_SUFFIXES


async def test_row_project_id_coerces_honestly() -> None:
    assert row_project_id({"project_id": "  abc  "}) == "abc"
    assert row_project_id({"project_id": ""}) is None
    assert row_project_id({"project_id": None}) is None
    assert row_project_id({}) is None
