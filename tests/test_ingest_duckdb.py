"""Hermetic DuckDB ingest E2E (storage portability program, sp-ingest-port).

ONE pipeline, DuckDB backend: the full ingest (partition -> chunk -> embed
-> extract -> store) runs against a tmp ``.duckdb`` file through the
``DuckDbGateway``, with the embed and extract stages stubbed (no Ollama,
no Postgres, no network). The fixture is a small HTML document ingested
through the web_structure strategy (heading-section chunks, OG metadata),
so the run covers two chunks, a pipeline-carried document date, and the
DuckDB ledger-based dedupe probe (migration 030). Verified:

* documents/chunks/chunks_vectors/entities/relations/ingest_runs rows land
  with the same envelope the Postgres path reports;
* an unchanged re-ingest skips honestly and changes nothing;
* a shrunken document updates the surviving chunk and tombstones the rest;
* ``document_date`` rides the first insert (the DuckDB documents row is
  immutable once referenced, so the per-upsert refresh stays Postgres-only);
* zero-vector (degraded) embeddings still STORE, flagged honestly;
* a concurrent same-content run under the in-process advisory lock skips.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("duckdb")

from corpus_kb._setup.migrate import run_migrations
from corpus_kb.extraction import ExtractStats
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string, vector_literal
from corpus_kb.storage.gateway import DuckDbGateway
from corpus_kb.tools import ingest_common
from corpus_kb.tools.ingest_common import advisory_lock_ctx
from corpus_kb.tools.ingest_store import PostgresIngestStore
from corpus_kb.utils.models import Chunk, Document, Entity, Relation

pytestmark = pytest.mark.asyncio

TENANT = "00000000-0000-0000-0000-000000000001"
STUB_DIMS = 2560
DOC_PATH = "doc-under-test.html"

DOC_TEXT = """<html><head>
<meta property="article:published_time" content="2024-01-15T08:00:00Z">
</head><body>
<h1>Alpha</h1>
<p>First section body text with enough words to stand alone as a chunk.</p>
<h1>Beta</h1>
<p>Second section body text, plain html, inside the same document.</p>
</body></html>"""

SHRUNKEN_TEXT = """<html><head>
<meta property="article:published_time" content="2024-01-15T08:00:00Z">
</head><body>
<h1>Alpha</h1>
<p>One section now; the Beta chunk must be tombstoned.</p>
</body></html>"""


def _stub_vector(text: str) -> list[float]:
    """A deterministic, never-all-zero vector derived from the chunk text."""
    seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)
    return [((seed + i) % 997) / 997.0 + 0.001 for i in range(STUB_DIMS)]


def _pipeline_config() -> dict[str, Any]:
    return {
        "database": {"backend": "duckdb"},
        "embedding": {"model": "stub-embed", "dimensions": STUB_DIMS},
        "graph": {"extract_entities": True},
        "ingest": {"contextualize": {"enabled": False}},
    }


def _stub_stages(monkeypatch: pytest.MonkeyPatch, *, zero_vectors: bool = False) -> None:
    """Replace the embed and extract stages with deterministic doubles."""

    async def fake_embed(
        chunks: list[Chunk],
        config: dict[str, Any],
        pool: Any = None,
        text_override: dict[str, str] | None = None,
        document_text: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> tuple[bool, str | None]:
        for chunk in chunks:
            source = (text_override or {}).get(str(chunk.chunk_id), chunk.text)
            chunk.embedding = [0.0] * STUB_DIMS if zero_vectors else _stub_vector(source)
        if meta is not None:
            meta["embedding_mode"] = "pooled"
        return zero_vectors, (
            "Connection failed: stub embedder returned zero vectors" if zero_vectors else None
        )

    async def fake_extract(
        chunks: list[Chunk],
        ontology: Any,
        source_document_id: str,
        config: dict[str, Any],
        pool: Any = None,
    ) -> tuple[list[Entity], list[Relation], str, ExtractStats]:
        alpha = Entity(
            name="Alpha",
            entity_type="CONCEPT",
            source_type="text",
            source_document_id=source_document_id,
            extractor_id="stub",
        )
        beta = Entity(
            name="Beta",
            entity_type="CONCEPT",
            source_type="text",
            source_document_id=source_document_id,
            extractor_id="stub",
        )
        relation = Relation(
            source_entity_id=alpha.entity_id,
            target_entity_id=beta.entity_id,
            relation_type="REFERENCES",
            chunk_id=chunks[0].chunk_id if chunks else None,
            extractor_id="stub",
            confidence=0.9,
        )
        stats = ExtractStats(
            backend="stub",
            requested_entities=2,
            kept_entities=2,
            requested_relations=1,
            kept_relations=1,
        )
        return [alpha, beta], [relation], "stub", stats

    monkeypatch.setattr(ingest_common, "embed_chunks", fake_embed)
    monkeypatch.setattr(ingest_common, "extract_with_fallback", fake_extract)


@pytest.fixture
async def duckdb_ingest_gateway(tmp_path: Path):
    """A migrated tmp .duckdb file behind one DuckDbGateway."""
    path = tmp_path / "ingest-e2e.duckdb"
    await run_migrations(duckdb_path_to_conn_string(str(path)))
    gateway = await DuckDbGateway.connect(str(path))
    try:
        yield gateway
    finally:
        await gateway.close()
        # Close BEFORE delete: duckdb keeps a .wal sidecar while open.
        for suffix in ("", ".wal"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)


async def _run(gateway: Any, text: str) -> dict[str, Any]:
    return await ingest_common.run_pipeline(
        text,
        "text",
        DOC_PATH,
        _pipeline_config(),
        gateway,
        TENANT,
        strategy_name="web_structure",
    )


async def test_ingest_duckdb_e2e_rows_and_envelope(
    duckdb_ingest_gateway: DuckDbGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_stages(monkeypatch)
    gw = duckdb_ingest_gateway
    result = await _run(gw, DOC_TEXT)

    assert result["status"] == "success"
    for key in (
        "document_id",
        "path",
        "source_type",
        "size_bytes",
        "chunk_count",
        "entity_count",
        "relation_count",
        "pg_chunk_count",
        "pg_vector_count",
        "degraded",
        "extractor_id",
        "entities",
        "errors",
        "run_id",
        "strategy",
        "strategy_basis",
        "embedding_mode",
        "extraction",
        "shadow_chunk_count",
        "shadow_vector_count",
    ):
        assert key in result, f"envelope key missing: {key}"
    assert result["chunk_count"] == 2
    assert result["entity_count"] == 2
    assert result["relation_count"] == 1
    assert result["degraded"] is False

    docs = await gw.fetch(
        "SELECT * FROM documents WHERE tenant_id = $1 AND source = $2",
        TENANT,
        DOC_PATH,
    )
    assert len(docs) == 1
    stored_hash: str = docs[0]["file_hash"]
    assert len(stored_hash) == 64 and all(c in "0123456789abcdef" for c in stored_hash)
    # DuckDB delta: the documents row is written once (chunk_count stays at
    # its begin_run value); run freshness lives in the ledger. The READ
    # path computes the real number from chunks (list_documents test below).
    assert docs[0]["chunk_count"] == 0
    # The pipeline-carried date rides the FIRST insert on DuckDB.
    assert docs[0]["document_date"] is not None
    assert str(docs[0]["document_date"])[:10] == "2024-01-15"

    chunks = await gw.fetch(
        "SELECT chunk_index, text, chunk_hash, degraded, tombstoned_at "
        "FROM chunks WHERE tenant_id = $1 AND doc_id = $2 ORDER BY chunk_index",
        TENANT,
        result["document_id"],
    )
    assert [row["chunk_index"] for row in chunks] == [0, 1]
    assert all(row["degraded"] is False for row in chunks)

    vectors = await gw.fetch(
        "SELECT dimensions, degraded, embedding FROM chunks_vectors "
        "WHERE tenant_id = $1 ORDER BY chunk_id",
        TENANT,
    )
    assert len(vectors) == 2
    assert all(row["dimensions"] == STUB_DIMS for row in vectors)
    assert all(row["degraded"] is False for row in vectors)
    assert all(len(row["embedding"]) == STUB_DIMS for row in vectors)

    entities = await gw.fetch(
        "SELECT name FROM entities WHERE tenant_id = $1 ORDER BY name", TENANT
    )
    assert [row["name"] for row in entities] == ["Alpha", "Beta"]

    relations = await gw.fetch(
        "SELECT relation_type, confidence FROM relations WHERE tenant_id = $1", TENANT
    )
    assert len(relations) == 1
    assert relations[0]["relation_type"] == "REFERENCES"

    runs = await gw.fetch(
        "SELECT status, source FROM ingest_runs WHERE tenant_id = $1 ORDER BY started_at, run_id",
        TENANT,
    )
    assert len(runs) == 1
    assert runs[0]["status"] == "completed"
    assert runs[0]["source"] == DOC_PATH
    # Deterministic ordering: the same query answers identically on re-read.
    again = await gw.fetch(
        "SELECT chunk_index, text FROM chunks WHERE tenant_id = $1 AND doc_id = $2 "
        "ORDER BY chunk_index",
        TENANT,
        result["document_id"],
    )
    assert [dict(row) for row in again] == [
        {"chunk_index": row["chunk_index"], "text": row["text"]} for row in chunks
    ]


async def test_list_documents_reports_real_chunk_count_on_duckdb(
    duckdb_ingest_gateway: DuckDbGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stored documents.chunk_count stays at its begin-run zero on
    DuckDB (the row is written once), but the READ path answers the real
    number: list_documents computes it from chunks over the gateway."""
    from corpus_kb.tools.ingest_tools import list_documents

    _stub_stages(monkeypatch)
    result = await _run(duckdb_ingest_gateway, DOC_TEXT)
    assert result["status"] == "success"

    envelope = await list_documents(
        duckdb_ingest_gateway,
        _pipeline_config(),
        tenant_id=TENANT,
        limit=10,
        offset=0,
    )
    assert envelope["status"] == "success"
    docs = envelope["result"]
    assert len(docs) == 1
    assert docs[0]["chunk_count"] == 2


async def test_ingest_duckdb_reingest_unchanged_skips(
    duckdb_ingest_gateway: DuckDbGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_stages(monkeypatch)
    gw = duckdb_ingest_gateway
    first = await _run(gw, DOC_TEXT)
    assert first["status"] == "success"

    second = await _run(gw, DOC_TEXT)
    assert second["status"] == "skipped"
    assert second["reason"] == "unchanged"
    assert second["document_id"] == first["document_id"]

    assert (await gw.fetchval("SELECT count(*) FROM chunks WHERE tenant_id = $1", TENANT)) == 2
    assert (await gw.fetchval("SELECT count(*) FROM ingest_runs WHERE tenant_id = $1", TENANT)) == 1


async def test_ingest_duckdb_changed_then_stale_content_heals(
    duckdb_ingest_gateway: DuckDbGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shrink the document, then re-offer the original: the probe never lies.

    The ledger-based probe keys on the NEWEST run per source, so stale
    content (a hash whose run is no longer the newest) re-ingests instead
    of skipping onto wrong-shaped rows.
    """
    _stub_stages(monkeypatch)
    gw = duckdb_ingest_gateway
    assert (await _run(gw, DOC_TEXT))["status"] == "success"

    shrunken = await _run(gw, SHRUNKEN_TEXT)
    assert shrunken["status"] == "success"
    assert shrunken["chunk_count"] == 1
    rows = await gw.fetch(
        "SELECT chunk_index, tombstoned_at FROM chunks WHERE tenant_id = $1 ORDER BY chunk_index",
        TENANT,
    )
    assert [row["chunk_index"] for row in rows] == [0, 1]
    assert rows[0]["tombstoned_at"] is None
    assert rows[1]["tombstoned_at"] is not None

    # Re-offer the ORIGINAL content: chunk 0 (changed hash) rebuilds live;
    # chunk 1 (hash unchanged) keeps its stored row, tombstoned state and
    # stored id included — the shared no-op contract, on both backends.
    healed = await _run(gw, DOC_TEXT)
    assert healed["status"] == "success"
    assert healed["chunk_count"] == 2
    rows = await gw.fetch(
        "SELECT chunk_index, tombstoned_at FROM chunks WHERE tenant_id = $1 ORDER BY chunk_index",
        TENANT,
    )
    assert rows[0]["tombstoned_at"] is None
    assert rows[1]["tombstoned_at"] is not None
    assert (await gw.fetchval("SELECT count(*) FROM ingest_runs WHERE tenant_id = $1", TENANT)) == 3


async def test_ingest_duckdb_document_row_immutable_after_insert(
    duckdb_ingest_gateway: DuckDbGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DuckDB documents rows are FK-referenced: update_document is a no-op.

    The per-upsert document_date refresh (COALESCE rule) stays Postgres-only;
    on DuckDB the first insert carries the date and the repair path must not
    raise when it cannot rewrite the row.
    """
    _stub_stages(monkeypatch)
    gw = duckdb_ingest_gateway
    result = await _run(gw, DOC_TEXT)
    doc_id = result["document_id"]

    document = Document(path=DOC_PATH, source_type="text", content=DOC_TEXT, size_bytes=10)
    document.document_id = doc_id
    document.metadata["document_date"] = "2030-06-01"
    store = PostgresIngestStore(gw, TENANT)
    await store.update_document(document, "new-hash")  # must not raise
    assert (
        str(await gw.fetchval("SELECT document_date FROM documents WHERE doc_id = $1", doc_id))[:10]
        == "2024-01-15"
    )


async def test_ingest_duckdb_zero_vectors_store_degraded(
    duckdb_ingest_gateway: DuckDbGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_stages(monkeypatch, zero_vectors=True)
    gw = duckdb_ingest_gateway
    result = await _run(gw, DOC_TEXT)

    # Zero vectors are the embedder-failure marker: the run is partial, the
    # ledger fails, and the rows still STORE with the degraded flag set.
    assert result["status"] == "partial"
    assert any(error.startswith("EmbeddingError") for error in result["errors"])
    vectors = await gw.fetch(
        "SELECT degraded, embedding FROM chunks_vectors WHERE tenant_id = $1", TENANT
    )
    assert len(vectors) == 2
    assert all(row["degraded"] is True for row in vectors)
    assert all(all(v == 0.0 for v in row["embedding"]) for row in vectors)
    runs = await gw.fetch("SELECT status FROM ingest_runs WHERE tenant_id = $1", TENANT)
    assert [row["status"] for row in runs] == ["failed"]


async def test_ingest_duckdb_concurrent_run_skips(
    duckdb_ingest_gateway: DuckDbGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_stages(monkeypatch)
    gw = duckdb_ingest_gateway
    # The pipeline keys the lock on sha256(plan.document_text) — recover it
    # from the stored documents row, then contend on exactly that key.
    first = await _run(gw, DOC_TEXT)
    assert first["status"] == "success"
    stored_hash: str = await gw.fetchval(
        "SELECT file_hash FROM documents WHERE doc_id = $1", first["document_id"]
    )

    async with advisory_lock_ctx(pool=None, tenant_id=TENANT, key=stored_hash) as acquired:
        assert acquired is True
        second = await _run(gw, DOC_TEXT)
    assert second["status"] == "skipped"
    assert second["reason"] == "concurrent-run"
    assert second["errors"] == []
    # No ledger row, no rows: the skip fired BEFORE begin_run.
    assert (await gw.fetchval("SELECT count(*) FROM ingest_runs WHERE tenant_id = $1", TENANT)) == 1


async def test_reembed_repairs_degraded_chunk_on_duckdb(
    duckdb_ingest_gateway: DuckDbGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REPAIR + DEGRADED_CLEAR on DuckDB, end to end.

    The degraded chunk leaves the scan, gets a fresh vector, and BOTH
    degraded flags clear — including the UPDATE on a chunks row that an
    already-committed chunks_vectors row references (the DuckDB FK-hazard
    path the Postgres CI covers implicitly).
    """
    from corpus_kb.tools import ingest_reembed

    gw = duckdb_ingest_gateway
    doc = "d0000000-0000-0000-0000-000000000009"
    bad = "c0000000-0000-0000-0000-000000000011"
    good = "c0000000-0000-0000-0000-000000000012"
    await gw.execute(
        "INSERT INTO documents (doc_id, tenant_id, source, source_type) VALUES (?, ?, ?, ?)",
        doc,
        TENANT,
        "reembed.md",
        "text",
    )
    for index, (cid, text) in enumerate(((bad, "degraded body"), (good, "healthy body"))):
        await gw.execute(
            "INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text)"
            " VALUES (?, ?, ?, ?, ?)",
            cid,
            TENANT,
            doc,
            index,
            text,
        )
        embedding = (
            vector_literal([0.0] * STUB_DIMS) if cid == bad else vector_literal(_stub_vector(text))
        )
        await gw.execute(
            "INSERT INTO chunks_vectors (chunk_id, tenant_id, embedding_model, embedding, degraded)"
            " VALUES (?, ?, ?, CAST(? AS FLOAT[2560]), ?)",
            cid,
            TENANT,
            "stale-model",
            embedding,
            cid == bad,
        )
    await gw.execute("UPDATE chunks SET degraded = TRUE WHERE chunk_id = ?", bad)

    async def fake_repair_embed(chunks, config, pool=None, text_override=None, **_kw):
        for chunk in chunks:
            chunk.embedding = _stub_vector(str(text_override[chunk.chunk_id]))
        return False, None

    monkeypatch.setattr(ingest_reembed, "embed_chunks", fake_repair_embed)

    summary = await ingest_reembed.reembed_degraded(gw, _pipeline_config(), tenant_id=TENANT)
    assert summary == {"status": "success", "scanned": 1, "fixed": 1, "failed": 0}
    repaired = await gw.fetchrow(
        "SELECT degraded, embedding_model, dimensions FROM chunks_vectors WHERE chunk_id = ?",
        bad,
    )
    assert dict(repaired) == {
        "degraded": False,
        "embedding_model": "stub-embed",
        "dimensions": 2560,
    }
    assert (
        await gw.fetchval("SELECT degraded FROM chunks WHERE chunk_id = ?", bad)
    ) is False  # the referenced chunks row took the flag-clear UPDATE
    healthy = await gw.fetchrow(
        "SELECT degraded, embedding_model FROM chunks_vectors WHERE chunk_id = ?", good
    )
    assert dict(healthy) == {"degraded": False, "embedding_model": "stale-model"}
