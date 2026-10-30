"""Sink protocol for the pipeline's writes.

A sink is the narrow port the stages write through. ``ChunkSink`` wraps
the existing store (``tools/ingest_store.py``, which itself drives the
set-based ``tools/ingest_batch.py`` seam on Postgres and the per-row path
on DuckDB) VERBATIM: every method is a one-line delegation, no SQL is
rewritten here. ``PostgresIngestStore`` itself satisfies the protocol
structurally, so the store can be handed to a stage with or without the
wrapper.

``UnitSink`` (Wave 3) is the research twin: the same port over the
research store, delegating VERBATIM to the research storage seams. It
lives in ``unit_sink.py`` (one sink family per module) and is re-exported
here so importers keep one path.
"""

from __future__ import annotations

from typing import Protocol

from ...storage.gateway import SqlGateway
from ...utils.models import Chunk, Document, Entity, Relation
from ..ingest_store import PostgresIngestStore
from .unit_sink import UnitSink

__all__ = ["ChunkSink", "IngestSink", "UnitSink"]


class IngestSink(Protocol):
    """The write port the pipeline stages call."""

    @property
    def tenant_id(self) -> str: ...

    @property
    def gateway(self) -> SqlGateway: ...

    async def unchanged_doc_id(self, source: str, file_hash: str) -> str | None: ...

    async def begin_run(self, document: Document, file_hash: str) -> tuple[str, str]: ...

    async def finish_run(
        self,
        run_id: str,
        file_hash: str,
        stages: dict[str, object],
        errors: list[str],
    ) -> None: ...

    async def fail_run(self, run_id: str, error: str) -> None: ...

    async def update_document(self, document: Document, file_hash: str) -> None: ...

    async def store_chunks(self, chunks: list[Chunk]) -> set[str]: ...

    async def count_chunks_for_document(self, doc_id: str) -> int: ...

    async def store_vectors(
        self,
        chunks: list[Chunk],
        embedding_model: str,
        dimensions: int = 2560,
        vectors_1024: dict[str, list[float]] | None = None,
    ) -> int: ...

    async def store_entities(self, entities: list[Entity]) -> dict[str, str]: ...

    async def store_relations(
        self,
        relations: list[Relation],
        model_version: str | None = None,
        prompt_version: str | None = None,
    ) -> int: ...


class ChunkSink:
    """Delegate every stage write to the wrapped store, byte for byte.

    The wrapper adds no logic. It gives the stages one typed port and
    keeps the store (and its SQL) untouched for Wave 3 reuse.
    """

    def __init__(self, store: PostgresIngestStore) -> None:
        self._store = store

    @property
    def store(self) -> PostgresIngestStore:
        """The wrapped store (ledger, chunk, vector, entity, relation)."""
        return self._store

    @property
    def tenant_id(self) -> str:
        """The tenant all wrapped writes scope to."""
        return self._store.tenant_id

    @property
    def gateway(self) -> SqlGateway:
        """The wrapped store's SQL gateway."""
        return self._store.gateway

    async def unchanged_doc_id(self, source: str, file_hash: str) -> str | None:
        """Delegates to the store's dedupe probe."""
        return await self._store.unchanged_doc_id(source, file_hash)

    async def begin_run(self, document: Document, file_hash: str) -> tuple[str, str]:
        """Delegates to the store's ledger open."""
        return await self._store.begin_run(document, file_hash)

    async def finish_run(
        self,
        run_id: str,
        file_hash: str,
        stages: dict[str, object],
        errors: list[str],
    ) -> None:
        """Delegates to the store's ledger close."""
        await self._store.finish_run(run_id, file_hash, stages, errors)

    async def fail_run(self, run_id: str, error: str) -> None:
        """Delegates to the store's ledger fail path."""
        await self._store.fail_run(run_id, error)

    async def update_document(self, document: Document, file_hash: str) -> None:
        """Delegates to the store's document refresh."""
        await self._store.update_document(document, file_hash)

    async def store_chunks(self, chunks: list[Chunk]) -> set[str]:
        """Delegates to the store's chunk write."""
        return await self._store.store_chunks(chunks)

    async def count_chunks_for_document(self, doc_id: str) -> int:
        """Delegates to the store's per-document chunk count."""
        return await self._store.count_chunks_for_document(doc_id)

    async def store_vectors(
        self,
        chunks: list[Chunk],
        embedding_model: str,
        dimensions: int = 2560,
        vectors_1024: dict[str, list[float]] | None = None,
    ) -> int:
        """Delegates to the store's vector write."""
        return await self._store.store_vectors(
            chunks, embedding_model, dimensions=dimensions, vectors_1024=vectors_1024
        )

    async def store_entities(self, entities: list[Entity]) -> dict[str, str]:
        """Delegates to the store's entity upsert."""
        return await self._store.store_entities(entities)

    async def store_relations(
        self,
        relations: list[Relation],
        model_version: str | None = None,
        prompt_version: str | None = None,
    ) -> int:
        """Delegates to the store's relation insert."""
        return await self._store.store_relations(
            relations, model_version=model_version, prompt_version=prompt_version
        )
