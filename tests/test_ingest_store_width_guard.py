"""Dimension-switch guard: width-mismatched vector writes fail with a typed error.

Empirical contract (pgvector 0.8.3 / Postgres 16.14): INSERT of a 768-dim
vector into a ``vector(4096)`` column fails with SQLSTATE 22000 ("expected
4096 dimensions, not 768"). The shipped default model (nomic-embed-text,
768d) therefore cannot ingest into the shipped schema. The guard surfaces
that as a ``ConfigError`` (taxonomy: config_error) with the schema reality
and the config fix — never as a generic Postgres write failure.

All tests are hermetic: recording connection doubles, no database.
"""

from __future__ import annotations

from typing import cast

import asyncpg
import pytest

from corpus_kb.errors import ConfigError
from corpus_kb.tools.ingest_store import (
    PostgresIngestStore,
    assert_vector_column_width,
)
from corpus_kb.utils.models import Chunk

TENANT = "00000000-0000-0000-0000-000000000003"
TYPEMOD_SQL_FRAGMENT = "chunks_vectors'::regclass"


class _WidthProbeTx:
    async def __aenter__(self) -> _WidthProbeConn:
        return self._conn

    async def __aexit__(self, exc_type: object, *exc: object) -> bool:
        return False

    def __init__(self, conn: _WidthProbeConn) -> None:
        self._conn = conn


class _WidthProbeConn:
    """Connection double answering the catalog probe with a fixed typmod row."""

    def __init__(
        self,
        typmod_row: dict[str, object] | None,
        insert_error: Exception | None = None,
    ) -> None:
        self.typmod_row = typmod_row
        self.insert_error = insert_error
        self.inserts = 0

    async def execute(self, sql: str, *args: object) -> str:
        if "INSERT INTO chunks_vectors" in sql and self.insert_error is not None:
            raise self.insert_error
        return "OK"

    async def fetchrow(self, sql: str, *args: object) -> dict[str, object] | None:
        if "atttypmod" in sql:
            return self.typmod_row
        return None

    def transaction(self) -> _WidthProbeTx:
        return _WidthProbeTx(self)


class _WidthProbePool:
    def __init__(self, conn: _WidthProbeConn) -> None:
        self._conn = conn

    def acquire(self):  # noqa: ANN201 - async-context-manager pool double
        return _PooledConn(self._conn)


class _PooledConn:
    def __init__(self, conn: _WidthProbeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _WidthProbeConn:
        return self._conn

    async def __aexit__(self, exc_type: object, *exc: object) -> bool:
        return False


def _chunk(embedding: list[float] | None = None) -> Chunk:
    chunk = Chunk(document_id="d1", text="hello", source_type="text")
    chunk.embedding = embedding
    return chunk


async def test_width_guard_raises_config_error_on_mismatch() -> None:
    """A 768-dim write against vector(4096) raises ConfigError before any insert."""
    conn = _WidthProbeConn({"width": 4096, "typ": "vector(4096)"})
    store = PostgresIngestStore(cast(asyncpg.Pool, _WidthProbePool(conn)), TENANT)
    with pytest.raises(ConfigError) as excinfo:
        await store.store_vectors([_chunk([0.1, 0.2])], "nomic-embed-text", dimensions=768)
    message = str(excinfo.value)
    assert "4096" in message
    assert "768" in message
    remediation = excinfo.value.remediation
    assert "embedding.model" in remediation
    assert "embedding.dimensions" in remediation
    assert conn.inserts == 0


async def test_width_guard_allows_matching_dimensions() -> None:
    """A width-matched write proceeds through the normal insert path."""
    conn = _WidthProbeConn({"width": 4096, "typ": "vector(4096)"})
    store = PostgresIngestStore(cast(asyncpg.Pool, _WidthProbePool(conn)), TENANT)
    count = await store.store_vectors(
        [_chunk([0.1, 0.2])], "qwen3-embedding:8b-q8_0", dimensions=4096
    )
    assert count == 1


async def test_width_guard_defers_when_table_missing() -> None:
    """No catalog row (pre-migration database): the guard stays silent."""
    conn = _WidthProbeConn(None)
    store = PostgresIngestStore(cast(asyncpg.Pool, _WidthProbePool(conn)), TENANT)
    count = await store.store_vectors([_chunk([0.1])], "nomic-embed-text", dimensions=768)
    assert count == 1


async def test_dataerror_translated_to_config_error() -> None:
    """A pgvector width DataError inside the loop becomes the typed ConfigError."""
    conn = _WidthProbeConn(
        {"width": 4096, "typ": "vector(4096)"},
        insert_error=asyncpg.DataError("expected 4096 dimensions, not 1024"),
    )
    store = PostgresIngestStore(cast(asyncpg.Pool, _WidthProbePool(conn)), TENANT)
    with pytest.raises(ConfigError) as excinfo:
        await store.store_vectors(
            [_chunk([0.1])], "qwen3-embedding:0.6b", dimensions=4096
        )
    assert "1024" in str(excinfo.value)
    assert "4096" in str(excinfo.value)
    assert "embedding.model" in excinfo.value.remediation


async def test_assert_vector_column_width_probe_is_advisory() -> None:
    """A failing catalog probe never raises; the INSERT reports its own error."""

    class _BrokenCatalogConn(_WidthProbeConn):
        async def fetchrow(self, sql: str, *args: object) -> dict[str, object] | None:
            if "atttypmod" in sql:
                raise RuntimeError("catalog unavailable")
            return None

    conn = _BrokenCatalogConn(None)
    await assert_vector_column_width(conn, 768, "nomic-embed-text")  # must not raise
