"""SearchResult round-trips the textbook citation + units metadata (#271).

The textbook strategy writes two chunk-metadata keys — ``citation`` (a
plain-English page citation) and ``units`` (the sorted unit names, today
``["table"]``). ``chunks.metadata`` stores them, but the search arms never
selected the column, so the keys never reached consumers. Pins:

* the model carries both optional keys and defaults them to None;
* the hybrid fusion re-attaches them from the arm rows' metadata JSON;
* ``search_similar`` and ``search_context`` carry them on both engines;
* a corpus without the metadata keeps the result-item key set unchanged
  (both keys present, value None) — the byte-identical envelope shape.

Hermetic: dict-row mocks for the Postgres shapes, one tmp DuckDB file
with the full migration mirror for the real round-trip.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from corpus_kb.domain.models import (
    SearchContextQuery,
    SearchQuery,
    SearchResult,
    SearchSimilarQuery,
)
from corpus_kb.handlers.query_crud import _expand_context, _similar_results
from corpus_kb.handlers.query_stages import fuse_outcome_legs
from corpus_kb.storage.duckdb_gateway import DuckDbGateway
from corpus_kb.storage.search_backend import SearchOutcome

pytest.importorskip("duckdb")

CITATION = "pp. 34-36 - Section 2.1"
UNITS = ["table"]
TEXTBOOK_METADATA = json.dumps({"citation": CITATION, "units": UNITS})
TENANT1 = "a0000000-0000-0000-0000-000000000001"
DOC1 = "d0000000-0000-0000-0000-000000000001"
C_BOOK = "c0000000-0000-0000-0000-000000000001"  # textbook chunk: has the keys
C_PLAIN = "c0000000-0000-0000-0000-000000000002"  # plain chunk: keys absent


# ---------------------------------------------------------------------------
# The model: optional keys, None defaults, stable serialization
# ---------------------------------------------------------------------------


def _result(**overrides: object) -> SearchResult:
    r = SearchResult(chunk_id=uuid4(), text="t", score=0.5, source="s", doc_id=uuid4())
    return r.model_copy(update=dict(overrides))


def test_model_defaults_citation_and_units_to_none() -> None:
    r = _result()
    assert r.citation is None and r.units is None
    dumped = r.model_dump(mode="json")
    assert dumped["citation"] is None and dumped["units"] is None


def test_model_carries_populated_citation_and_units() -> None:
    r = _result(citation=CITATION, units=UNITS)
    assert r.citation == CITATION and r.units == UNITS


# ---------------------------------------------------------------------------
# The fusion provenance re-attach (handlers/query_stages.py)
# ---------------------------------------------------------------------------


def _outcome(*rows: dict[str, object]) -> SearchOutcome:
    return SearchOutcome(
        vector_rows=[],
        lexical_rows=list(rows),
        excluded_count=0,
        vector_index_mode="pg",
        lexical_index_mode="pg",
    )


def _row(chunk: str, metadata: str | None) -> dict[str, object]:
    row: dict[str, object] = {
        "chunk_id": UUID(chunk),
        "text": "textbook hit",
        "doc_id": UUID(DOC1),
        "source": "book.pdf",
        "file_path": None,
        "start_line": 1,
        "end_line": 2,
        "chunk_index": 0,
        "heading_path": None,
        "score": 1.0,
    }
    if metadata is not None:
        row["metadata"] = metadata
    return row


def test_fusion_reattaches_citation_and_units_from_metadata() -> None:
    results = fuse_outcome_legs([_outcome(_row(C_BOOK, TEXTBOOK_METADATA))], k=5, rrf_k=60)
    assert len(results) == 1
    assert results[0].citation == CITATION
    assert results[0].units == UNITS


def test_fusion_without_metadata_keeps_both_keys_none() -> None:
    results = fuse_outcome_legs([_outcome(_row(C_PLAIN, None))], k=5, rrf_k=60)
    assert results[0].citation is None and results[0].units is None


# ---------------------------------------------------------------------------
# The search_similar + search_context mappers (handlers/query_crud.py)
# ---------------------------------------------------------------------------


def test_similar_results_maps_metadata() -> None:
    rows = [_row(C_BOOK, TEXTBOOK_METADATA), _row(C_PLAIN, json.dumps({}))]
    results = _similar_results(rows)
    assert results[0].citation == CITATION and results[0].units == UNITS
    assert results[1].citation is None and results[1].units is None


async def test_expand_context_maps_metadata() -> None:
    class _Reader:
        async def fetch(self, _sql: str, *_args: object) -> list[dict[str, object]]:
            return [_row(C_BOOK, TEXTBOOK_METADATA)]

    base = _result(chunk_id=UUID(C_PLAIN))
    query = SearchContextQuery(query="q", context_chunks=2, tenant_id=UUID(TENANT1))
    expanded = await _expand_context(_Reader(), [base], query)
    assert len(expanded) == 2
    assert expanded[1].citation == CITATION and expanded[1].units == UNITS


# ---------------------------------------------------------------------------
# The real round-trip on the DuckDB mirror (hybrid search + similar)
# ---------------------------------------------------------------------------


def _one_hot(token: str, dims: int = 2560) -> list[float]:
    vec = [0.0] * dims
    vec[sum(ord(ch) for ch in token) % dims] = 1.0
    return vec


def _handler_config() -> dict[str, object]:
    return {
        "embedding": {"provider": "ollama"},
        "retrieval": {"exclude_degraded": True},
        "search": {"reranker": "none"},
        "reranking": {"enabled": False},
    }


class _StubEmbedder:
    """Deterministic one-hot embedder keyed on the query's first token."""

    def embed(self, text: str) -> list[float]:
        return _one_hot(text.split()[0] if text.split() else "quick")


async def _seed_textbook_corpus(gateway: DuckDbGateway) -> None:
    from corpus_kb.storage.duckdb_support import vector_literal

    await gateway.execute(
        "INSERT INTO documents (doc_id, tenant_id, source, source_type, file_hash)"
        " VALUES (?, ?, ?, ?, ?)",
        DOC1,
        TENANT1,
        "book.pdf",
        "text",
        "hash-book",
    )
    rows = [
        (C_BOOK, "caption says the quick table", TEXTBOOK_METADATA),
        (C_PLAIN, "the quick plain text", None),
    ]
    for i, (cid, text, metadata) in enumerate(rows):
        await gateway.execute(
            "INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text, metadata)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            cid,
            TENANT1,
            DOC1,
            i,
            text,
            metadata,
        )
        await gateway.execute(
            "INSERT INTO chunks_vectors (chunk_id, tenant_id, embedding_model, embedding,"
            " degraded) VALUES (?, ?, ?, CAST(? AS FLOAT[2560]), ?)",
            cid,
            TENANT1,
            "stub",
            vector_literal(_one_hot("quick")),
            False,
        )


async def test_duckdb_search_round_trips_citation_and_units(tmp_path: Path) -> None:
    from corpus_kb._setup.migrate import run_duckdb_migrations
    from corpus_kb.handlers.query_handler import QueryHandler
    from corpus_kb.handlers.query_integrity import envelope_to_payload
    from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string

    db = tmp_path / "meta.duckdb"
    run_duckdb_migrations(duckdb_path_to_conn_string(str(db)))
    gateway = await DuckDbGateway.connect(duckdb_path_to_conn_string(str(db)))
    try:
        await _seed_textbook_corpus(gateway)
        handler = QueryHandler(gateway, embedder=_StubEmbedder(), config=_handler_config())
        results = await handler.handle_search(
            SearchQuery(query="quick", k=10, tenant_id=UUID(TENANT1))
        )
        by_id = {str(r.chunk_id): r for r in results}
        assert by_id[C_BOOK].citation == CITATION
        assert by_id[C_BOOK].units == UNITS
        assert by_id[C_PLAIN].citation is None and by_id[C_PLAIN].units is None

        # Envelope shape: the key set of every result item is identical
        # with and without the metadata (the without-corpus keys hold None).
        payload = envelope_to_payload(
            await handler.handle_search_with_meta(
                SearchQuery(query="quick", k=10, tenant_id=UUID(TENANT1))
            )
        )
        keys = [sorted(item) for item in payload["result"]]
        assert keys[0] == keys[1]

        similar = await handler.handle_search_similar(
            SearchSimilarQuery(chunk_id=UUID(C_BOOK), tenant_id=UUID(TENANT1), k=10)
        )
        similar_by_id = {str(r.chunk_id): r for r in similar}
        assert similar_by_id[C_PLAIN].citation is None
        assert similar_by_id[C_PLAIN].units is None

        context = await handler.handle_search_context(
            SearchContextQuery(query="quick", k=5, tenant_id=UUID(TENANT1))
        )
        context_by_id = {str(r.chunk_id): r for r in context}
        assert context_by_id[C_PLAIN].citation is None
        assert context_by_id[C_PLAIN].units is None
    finally:
        await gateway.close()


def test_arm_select_lists_stay_byte_equal_across_engines() -> None:
    # The hermetic parity pin (test_duckdb_search) enforces byte equality;
    # this file pins that the metadata column is part of BOTH lists, so a
    # one-sided drop fails here first with a clearer message.
    from corpus_kb.handlers import query_vector_rows
    from corpus_kb.storage import duckdb_search

    assert duckdb_search.SELECT_LIST == query_vector_rows.SELECT_LIST
    assert "chunks.metadata" in query_vector_rows.SELECT_LIST
