"""Hermetic DuckDB extraction-cache parity (W2.5 S5).

The Postgres path caches per-chunk extraction results keyed by
``(tenant_id, chunk_hash, backend, model_version, prompt_version)`` and
skips unchanged chunks on re-ingest. The DuckDB path must answer the
same keys with the same hits through the one gateway seam (same table,
migration 031 twin; same statement set; cache writes ride the gateway
statement gate — no side connections).

Cross-engine equivalence argument (test 4 pins it against live Postgres,
skipping honestly when Postgres is down): the cache statements are one
SQL text run verbatim on both engines — the DuckDB gateway's cast and
parameter shims are the only translation — and the key tuple is computed
engine-side once, so equal keys must answer equal hits or the parity
test fails.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("duckdb")

from corpus_kb._setup.migrate import run_migrations
from corpus_kb.extraction import ExtractStats
from corpus_kb.extraction.cache import ExtractionCache
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string
from corpus_kb.storage.gateway import DuckDbGateway
from corpus_kb.storage.tenant_conn import tenant_connection
from corpus_kb.tools import ingest_common
from corpus_kb.utils.models import Chunk, Entity, Relation

pytestmark = pytest.mark.asyncio

TENANT = "00000000-0000-0000-0000-000000000001"
OTHER_TENANT = "00000000-0000-0000-0000-000000000002"
STUB_DIMS = 2560
SOURCE_A = "cache-doc-a.html"
SOURCE_B = "cache-doc-b.html"
SOURCE_C = "cache-doc-c.html"

DOC_TEXT = """<html><body>
<h1>Alpha</h1>
<p>First section body text with enough words to stand alone as a chunk.</p>
<h1>Beta</h1>
<p>Second section body text, plain html, inside the same document.</p>
</body></html>"""

CHANGED_TEXT = """<html><body>
<h1>Alpha</h1>
<p>First section body text with enough words to stand alone as a chunk.</p>
<h1>Beta</h1>
<p>Changed second section body; only this chunk may re-extract.</p>
</body></html>"""


def _pipeline_config() -> dict[str, Any]:
    return {
        "database": {"backend": "duckdb"},
        "embedding": {"model": "stub-embed", "dimensions": STUB_DIMS},
        "graph": {"extract_entities": True},
        "ingest": {"contextualize": {"enabled": False}},
    }


def _stub_vector(text: str) -> list[float]:
    seed = int(text.encode("utf-8").hex()[:8], 16)
    return [((seed + i) % 997) / 997.0 + 0.001 for i in range(STUB_DIMS)]


@pytest.fixture
async def duckdb_gateway(tmp_path: Path):
    """A migrated tmp .duckdb file behind one DuckDbGateway."""
    path = tmp_path / "cache-parity.duckdb"
    await run_migrations(duckdb_path_to_conn_string(str(path)))
    gateway = await DuckDbGateway.connect(str(path))
    try:
        yield gateway
    finally:
        await gateway.close()
        for suffix in ("", ".wal"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)


def _stub_extract(monkeypatch: pytest.MonkeyPatch, calls: list[list[str]]) -> None:
    """Stub embed + extract; the extract stub records pending chunk hashes."""

    async def fake_embed(chunks: list[Chunk], config: dict[str, Any], **_kw: Any):
        for chunk in chunks:
            chunk.embedding = _stub_vector(chunk.text)
        return False, None

    async def fake_extract(
        chunks: list[Chunk],
        ontology: Any,
        source_document_id: str,
        config: dict[str, Any],
        pool: Any = None,
    ) -> tuple[list[Entity], list[Relation], str, ExtractStats]:
        calls.append([str(c.chunk_hash) for c in chunks])
        entities = [
            Entity(
                name=f"E{index}-{str(chunk.chunk_hash)[:8]}",
                entity_type="CONCEPT",
                source_type="text",
                source_document_id=source_document_id,
                chunk_id=str(chunk.chunk_id),
                extractor_id="stub",
            )
            for index, chunk in enumerate(chunks)
        ]
        stats = ExtractStats(backend="stub", kept_entities=len(entities))
        return entities, [], "stub", stats

    monkeypatch.setattr(ingest_common, "embed_chunks", fake_embed)
    monkeypatch.setattr(ingest_common, "extract_with_fallback", fake_extract)


async def _run(gateway: DuckDbGateway, text: str, source: str) -> dict[str, Any]:
    return await ingest_common.run_pipeline(
        text, "text", source, _pipeline_config(), gateway, TENANT, strategy_name="web_structure"
    )


async def test_duckdb_reingest_serves_extraction_from_cache(
    duckdb_gateway: DuckDbGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same chunk text under a new source: zero extraction passes, cache replay."""
    gw = duckdb_gateway
    calls: list[list[str]] = []
    _stub_extract(monkeypatch, calls)

    first = await _run(gw, DOC_TEXT, SOURCE_A)
    assert first["status"] == "success"
    assert len(calls) == 1 and len(calls[0]) == 2
    # Honest-count note: a cold run reports the zero it served.
    assert any("0 of 2 chunks served from cache" in note for note in first["extraction"]["notes"])

    calls.clear()
    second = await _run(gw, DOC_TEXT, SOURCE_B)
    assert second["status"] == "success"
    assert calls == [], "the second ingest re-paid extraction for cached chunks"
    assert any(
        "all chunks served from extraction cache" in note for note in second["extraction"]["notes"]
    )
    assert second["entity_count"] == 2
    names = await gw.fetch("SELECT name FROM entities WHERE tenant_id = $1 ORDER BY name", TENANT)
    assert len(names) == 2
    relations = await gw.fetch(
        "SELECT chunk_id FROM relations WHERE tenant_id = $1 AND chunk_id IS NOT NULL", TENANT
    )
    doc_b_chunks = await gw.fetch(
        "SELECT c.chunk_id FROM chunks c JOIN documents d ON c.doc_id = d.doc_id "
        "WHERE c.tenant_id = $1 AND d.source = $2",
        TENANT,
        SOURCE_B,
    )
    doc_b_ids = {str(row["chunk_id"]) for row in doc_b_chunks}
    assert {str(row["chunk_id"]) for row in relations} <= doc_b_ids


async def test_duckdb_changed_doc_misses_only_changed_chunk(
    duckdb_gateway: DuckDbGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One changed section: exactly the changed chunk re-extracts."""
    gw = duckdb_gateway
    calls: list[list[str]] = []
    _stub_extract(monkeypatch, calls)

    first = await _run(gw, DOC_TEXT, SOURCE_C)
    assert first["status"] == "success"
    unchanged_hash = calls[0][0]

    calls.clear()
    second = await _run(gw, CHANGED_TEXT, SOURCE_C)
    assert second["status"] == "success"
    assert len(calls) == 1, "only the changed chunk may re-extract"
    assert calls[0] != [unchanged_hash]
    assert any("1 of 2 chunks served from cache" in note for note in second["extraction"]["notes"])


async def test_duckdb_cache_key_semantics_and_tenant_isolation(
    duckdb_gateway: DuckDbGateway,
) -> None:
    """The key tuple answers hits exactly; another tenant reads nothing."""
    gw = duckdb_gateway
    cache = ExtractionCache(gw, TENANT)
    key: dict[str, str] = {"backend": "stub-b", "model_version": "m1", "prompt_version": "p1"}
    written = await cache.put({"hash-a": ([_entity("Isolated")], [])}, **key)
    assert written == 1

    hits = await cache.get(["hash-a"], **key)
    assert set(hits) == {"hash-a"}
    assert hits["hash-a"][0][0].name == "Isolated"
    for miss in (
        {"backend": "stub-b", "model_version": "m1", "prompt_version": "p2"},
        {"backend": "stub-b", "model_version": "m2", "prompt_version": "p1"},
        {"backend": "other", "model_version": "m1", "prompt_version": "p1"},
    ):
        assert await cache.get(["hash-a"], **miss) == {}

    other = ExtractionCache(gw, OTHER_TENANT)
    assert await other.get(["hash-a"], **key) == {}, "tenant isolation leaked across tenants"


def _entity(name: str) -> Entity:
    return Entity(name=name, entity_type="CONCEPT", source_type="text")


async def test_extraction_cache_hits_match_across_engines(
    pg_pool, duckdb_gateway: DuckDbGateway
) -> None:
    """Equal keys answer equal hits on Postgres and DuckDB (see module doc)."""
    key: dict[str, str] = {"backend": "parity-probe", "model_version": "m", "prompt_version": "p"}
    entries = {"parity-hash": ([_entity("Parity")], [])}
    async with tenant_connection(pg_pool, TENANT) as conn:
        await conn.execute("DELETE FROM extraction_cache WHERE backend = $1", key["backend"])

    for store in (pg_pool, duckdb_gateway):
        cache = ExtractionCache(store, TENANT)
        await cache.put(entries, **key)

    hashes = ["parity-hash", "absent"]
    pg_hits = await ExtractionCache(pg_pool, TENANT).get(hashes, **key)
    duck_hits = await ExtractionCache(duckdb_gateway, TENANT).get(hashes, **key)
    assert set(pg_hits) == set(duck_hits) == {"parity-hash"}
    pg_names = [e.name for e in pg_hits["parity-hash"][0]]
    duck_names = [e.name for e in duck_hits["parity-hash"][0]]
    assert pg_names == duck_names == ["Parity"]
