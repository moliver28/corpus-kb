"""DuckDbRagBackend arms — hermetic on a tmp ``.duckdb`` (sp-command-port).

Vector arm with a stub embedder (zero-vector case included), lexical arm
over the fts ``match_bm25`` macro, and the fused arm assembled with the
exact fusion port :func:`corpus_kb.handlers.query_fusion.rrf_fuse`. Every
assertion pins deterministic ordering (score DESC, node id ASC).
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from corpus_kb.errors import InternalError
from corpus_kb.handlers.query_fusion import rrf_fuse
from corpus_kb.storage.duckdb_gateway import DuckDbGateway
from corpus_kb.storage.duckdb_rag import DuckDbRagBackend
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string
from corpus_kb.storage.rag_backend import RagBackend

DIM = 4


class StubEmbedder:
    """Fixed hash-free vectors: keyword pick, zero when unknown."""

    def __init__(self) -> None:
        self.by_keyword = {"alpha": [1.0, 0.0, 0.0, 0.0], "beta": [0.0, 1.0, 0.0, 0.0]}

    def embed(self, text: str) -> list[float]:
        for keyword, vector in self.by_keyword.items():
            if keyword in text:
                return list(vector)
        return [0.0] * DIM


@pytest.fixture
async def backend(tmp_path: Path):
    gw = await DuckDbGateway.connect(duckdb_path_to_conn_string(str(tmp_path / "rag.duckdb")))
    backend = DuckDbRagBackend(gw, StubEmbedder(), dimensions=DIM)
    await backend.initialize()
    yield backend
    await gw.close()


async def _seed(backend: DuckDbRagBackend) -> tuple[str, list[str]]:
    ids = [str(uuid4()) for _ in range(4)]
    chunks = [
        {"node_id": ids[0], "text": "alpha grain news", "embedding": [1.0, 0.0, 0.0, 0.0]},
        {"node_id": ids[1], "text": "alpha beta blend", "embedding": [0.9, 0.1, 0.0, 0.0]},
        {"node_id": ids[2], "text": "beta only text", "embedding": [0.0, 1.0, 0.0, 0.0]},
        {"node_id": ids[3], "text": "degraded zero row", "embedding": [0.0, 0.0, 0.0, 0.0]},
    ]
    # A UUID source id: rrf_fuse parses doc ids as UUIDs.
    source_id = str(uuid4())
    await backend.ingest(source_id, chunks)
    return source_id, ids


async def test_satisfies_rag_backend_protocol(backend: DuckDbRagBackend) -> None:
    assert isinstance(backend, RagBackend)
    assert await backend.health() is True


async def test_vector_arm_orders_by_score_then_id(backend: DuckDbRagBackend) -> None:
    _source, ids = await _seed(backend)
    hits = await backend.retrieve("alpha", top_k=10)
    assert [h.node_id for h in hits] == ids[:4]  # score DESC, node id ASC ties
    assert hits[0].score == pytest.approx(1.0)
    assert hits[1].score == pytest.approx(0.99388, abs=1e-4)
    assert hits[3].node_id == ids[3]  # the zero-vector row sorts LAST
    assert hits[3].score == -2.0  # the NaN-last parity sentinel


async def test_vector_arm_zero_query_is_deterministic(backend: DuckDbRagBackend) -> None:
    _source, ids = await _seed(backend)
    hits = await backend.retrieve("unknown", top_k=10)  # stub answers zero
    assert [h.node_id for h in hits] == sorted(ids)
    assert all(h.score == -2.0 for h in hits)


async def test_vector_arm_caps_top_k(backend: DuckDbRagBackend) -> None:
    _source, _ids = await _seed(backend)
    hits = await backend.retrieve("alpha", top_k=2)
    assert len(hits) == 2


async def test_lexical_arm_via_fts_macro(backend: DuckDbRagBackend) -> None:
    _source, ids = await _seed(backend)
    hits = await backend.retrieve_lexical("alpha", top_k=10)
    # Equal bm25 (same tf, same length) → the node id ASC tiebreak.
    assert [h.node_id for h in hits] == sorted([ids[0], ids[1]])
    misses = await backend.retrieve_lexical("unindexed-word", top_k=10)
    assert misses == []


async def test_source_filter_scopes_both_arms(backend: DuckDbRagBackend) -> None:
    _source, _ids = await _seed(backend)
    other = str(uuid4())
    await backend.ingest(
        other,
        [{"text": "alpha second source", "embedding": [1.0, 0.0, 0.0, 0.0]}],
    )
    filters = {"source_id": other}
    vector_hits = await backend.retrieve("alpha", top_k=10, filters=filters)
    assert [h.source_id for h in vector_hits] == [other]
    lexical_hits = await backend.retrieve_lexical("alpha", top_k=10, filters=filters)
    assert [h.source_id for h in lexical_hits] == [other]


async def test_fused_arm_reuses_rrf_fuse(backend: DuckDbRagBackend) -> None:
    _source, ids = await _seed(backend)
    vector_rows, lexical_rows = await backend.scored_arms("alpha", top_k=10)
    for rows in (vector_rows, lexical_rows):
        assert all({"chunk_id", "text", "source", "doc_id"} <= set(dict(row)) for row in rows)
    fused = rrf_fuse(lexical_rows, vector_rows, k=10)
    again = rrf_fuse(lexical_rows, vector_rows, k=10)
    assert fused == again  # deterministic
    # ids[0] and ids[1] appear in BOTH legs: they outrank the lexical-only
    # and vector-only rows under reciprocal rank fusion.
    top_two = {str(row.chunk_id) for row in fused[:2]}
    assert top_two == {ids[0], ids[1]}
    assert fused[0].score > fused[2].score


async def test_delete_source_removes_rows(backend: DuckDbRagBackend) -> None:
    source, _ids = await _seed(backend)
    await backend.delete_source(source)
    assert await backend.retrieve("alpha", top_k=10) == []
    assert await backend.retrieve_lexical("alpha", top_k=10) == []


async def test_ingest_embeds_when_embedding_missing(backend: DuckDbRagBackend) -> None:
    await backend.ingest("src3", [{"text": "alpha from embedder"}])
    hits = await backend.retrieve("alpha", top_k=5)
    assert len(hits) == 1
    assert hits[0].score == pytest.approx(1.0)


async def test_reingest_replaces_source_rows(backend: DuckDbRagBackend) -> None:
    await backend.ingest("src4", [{"text": "alpha old", "embedding": [1.0, 0.0, 0.0, 0.0]}])
    await backend.ingest("src4", [{"text": "alpha new", "embedding": [1.0, 0.0, 0.0, 0.0]}])
    hits = await backend.retrieve("alpha", top_k=5)
    assert [h.text for h in hits] == ["alpha new"]


async def test_uninitialized_backend_fails_honest(tmp_path: Path) -> None:
    gw = await DuckDbGateway.connect(duckdb_path_to_conn_string(str(tmp_path / "fresh.duckdb")))
    try:
        fresh = DuckDbRagBackend(gw, StubEmbedder(), dimensions=DIM)
        with pytest.raises(InternalError, match="initialize"):
            await fresh.retrieve("alpha", top_k=5)
        with pytest.raises(InternalError, match="initialize"):
            await fresh.delete_source("src1")
    finally:
        await gw.close()
