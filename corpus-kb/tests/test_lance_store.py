"""TDD tests for LanceDBStore (vectors-only)."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from src.storage.lance_store import LanceDBStore
from src.utils.models import Chunk

_DIM = 8


def _chunk(chunk_id: str, vector: list[float]) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        document_id="doc-1",
        text=f"text-{chunk_id}",
        source_type="text",
        embedding=vector,
    )


def test_add_chunks_then_search_returns_k_results(tmp_path: Path) -> None:
    """Adding three chunks and searching returns up to k Chunk-shaped rows."""
    store = LanceDBStore(str(tmp_path), _DIM)
    chunks = [
        _chunk("c1", [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        _chunk("c2", [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        _chunk("c3", [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
    ]

    store.add_chunks(chunks)
    query = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    results = store.search(query, k=2)

    assert len(results) == 2
    for result in results:
        assert result.embedding is not None
        assert len(result.embedding) == _DIM
        assert result.document_id == "doc-1"
        assert result.source_type == "text"
    assert results[0].chunk_id == "c1"


def test_table_schema_has_exactly_four_columns(tmp_path: Path) -> None:
    """The LanceDB table contains only chunk_id, vector, source_type, document_id."""
    store = LanceDBStore(str(tmp_path), _DIM)
    chunks = [_chunk("c1", [1.0] * _DIM)]
    store.add_chunks(chunks)

    table = store._db.open_table("chunks")
    names = table.schema.names
    assert set(names) == {"chunk_id", "vector", "source_type", "document_id"}


def test_reopening_same_uri_preserves_rows(tmp_path: Path) -> None:
    """A second LanceDBStore on the same URI sees previously added rows."""
    store1 = LanceDBStore(str(tmp_path), _DIM)
    chunks = [
        _chunk("c1", [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        _chunk("c2", [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
    ]
    store1.add_chunks(chunks)
    store1.close()

    store2 = LanceDBStore(str(tmp_path), _DIM)
    table = store2._db.open_table("chunks")
    assert cast(int, table.to_arrow().num_rows) == 2

    results = store2.search([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], k=10)
    assert len(results) == 2


def test_add_empty_chunks_raises_value_error(tmp_path: Path) -> None:
    """Passing an empty list to add_chunks raises ValueError."""
    store = LanceDBStore(str(tmp_path), _DIM)
    with pytest.raises(ValueError):
        store.add_chunks([])


def test_missing_embedding_falls_back_to_zero_vector(tmp_path: Path) -> None:
    """A chunk without an embedding is stored as a zero vector."""
    store = LanceDBStore(str(tmp_path), _DIM)
    chunk = Chunk(
        chunk_id="c1",
        document_id="doc-1",
        text="no-vector",
        source_type="text",
        embedding=None,
    )
    store.add_chunks([chunk])

    results = store.search([1.0] + [0.0] * (_DIM - 1), k=1)
    assert len(results) == 1
    assert results[0].embedding == [0.0] * _DIM
