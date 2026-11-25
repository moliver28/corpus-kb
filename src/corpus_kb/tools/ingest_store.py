"""Ingest write layer over the SqlGateway (storage portability WP).

Every document/chunk/vector/entity/relation write and the ``ingest_runs``
integrity ledger live here; ``ingest_common`` keeps only orchestration and
calls into :class:`PostgresIngestStore`. One pipeline, two backends: the
store takes an asyncpg pool or any ``SqlGateway``; backend SQL deltas live
in ``ingest_sql.py``, and the chunk/vector write bodies live in
``ingest_store_chunks.py``.

Ledger semantics (migration 022): ``begin_run`` opens a ``status='running'``
row with the content hash BEFORE any stage writes; ``finish_run`` closes it
LAST — completed only when the stage error list is empty. The dedupe probe
skips only content whose last run COMPLETED with no stage errors, so a
crashed or partial ingest is re-ingested, never enshrined.

Every method runs inside ``gateway.transaction(tenant_id)``: Postgres sets
the tenant GUC first (a LOCAL GUC on an autocommit connection evaporates,
which would leave later writes rejected on FORCE-RLS tables, migration
011+); DuckDB rides BEGIN..COMMIT plus the explicit tenant predicate.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

import asyncpg

from corpus_kb.contracts.model import DEFAULT_TENANT_ID
from corpus_kb.dimensions import EMBEDDING_DIMENSIONS

from ..analysis.dates import parse_date_value
from ..storage.gateway import SqlGateway
from ..utils.models import Chunk, Document, Entity, Relation
from . import ingest_batch
from .ingest_batch import entity_persisted_metadata
from .ingest_sql import ingest_sql_for, pg_vector_type_mod_probe
from .ingest_store_chunks import (
    _CHUNK_TOMBSTONE_SQL,
    store_chunks_duckdb,
    vector_row,
)
from .ingest_store_chunks import (
    store_vectors as _store_vector_rows,
)

logger = logging.getLogger(__name__)

_CHUNK_COUNT_SQL = (
    "SELECT COUNT(*) FROM chunks WHERE tenant_id = $1 AND doc_id = $2 AND tombstoned_at IS NULL"
)
_CLAIM_SQL = (
    "INSERT INTO project_claims (tenant_id, project_id, doc_id, source_project_id)"
    " VALUES ($1, $2, $3, $4)"
    " ON CONFLICT (tenant_id, project_id, doc_id) DO NOTHING"
    " RETURNING project_id"
)
_ORIGIN_SQL = "SELECT project_id::text FROM documents WHERE tenant_id = $1 AND doc_id = $2"


def gateway_backend(gateway: SqlGateway) -> str:
    """The backend a gateway drives (moved home: storage.gateway)."""
    from corpus_kb.storage.gateway import gateway_backend as _impl

    return _impl(gateway)


def as_gateway(source: asyncpg.Pool | SqlGateway) -> SqlGateway:
    """The one pool/gateway coercion (moved home: storage.gateway)."""
    from corpus_kb.storage.gateway import as_gateway as _impl

    return _impl(source)


async def assert_vector_column_width(
    gw: SqlGateway,
    dimensions: int,
    embedding_model: str,
) -> None:
    """Fail fast when ``dimensions`` cannot enter ``chunks_vectors.embedding``.

    The shipped Postgres schema fixes the column at halfvec(2560) (the
    native width of the default qwen3-embedding:4b), so a model emitting
    any other width CANNOT ingest. The probe raises a typed ConfigError
    before the per-chunk inserts fail mid-transaction. It is advisory:
    an unreadable catalog defers to the INSERT's own error.
    """
    await pg_vector_type_mod_probe(gw, dimensions, embedding_model)


def _document_date_param(document: Document) -> datetime | None:
    """The resolved date for the upsert's ``document_date`` (None -> NULL).

    The date rides in ``document.metadata`` (``document_date``, ISO text)
    so the Document model stays schema-stable. The COALESCE rule in the
    SQL decides the merge: a newly resolved date fills the column, a NULL
    never clears a stored date — on BOTH backends (per-upsert, not only
    on first insert).
    """
    return parse_date_value(document.metadata.get("document_date"))


def _document_title_param(document: Document) -> str | None:
    """The upsert's ``title`` bind value (metadata ``title``, None -> NULL).

    Same channel as the date: the pipeline stages put the per-book title
    into ``document.metadata`` (issue #222), the store lifts it into the
    documents.title column. The SQL's COALESCE keeps a stored title when
    this ingest carries none.
    """
    title = document.metadata.get("title")
    return title if isinstance(title, str) and title else None


class PostgresIngestStore:
    """All ingest pipeline writes over one SqlGateway (Postgres or DuckDB)."""

    def __init__(
        self,
        pool: asyncpg.Pool | SqlGateway,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> None:
        self._gateway = as_gateway(pool)
        self._tenant_id = tenant_id
        self._sql = ingest_sql_for(gateway_backend(self._gateway))

    @property
    def tenant_id(self) -> str:
        """The tenant all store operations scope to (RLS GUC value)."""
        return self._tenant_id

    @property
    def gateway(self) -> SqlGateway:
        """The underlying SQL gateway (statement writes reuse it)."""
        return self._gateway

    async def unchanged_doc_id(self, source: str, file_hash: str) -> str | None:
        """Return the doc_id when this exact content was already ingested cleanly.

        A documents row for this source whose file_hash still matches AND a
        completed ``ingest_runs`` row for that hash with no stage errors must
        hold together. A running, failed, or errored past run does NOT count:
        re-ingest heals it instead of silently skipping.
        """
        async with self._gateway.transaction(self._tenant_id) as tx:
            row = await tx.fetchrow(
                self._sql.unchanged_doc_sql,
                self._tenant_id,
                source,
                file_hash,
            )
            return row["doc_id"] if row is not None else None

    async def unchanged_hash_doc_id(self, file_hash: str) -> str | None:
        """The hash-first dedupe probe (F-06): same clean-run requirement as
        ``unchanged_doc_id``, but the bind is the hash alone, so a match
        answers across path spellings.
        """
        async with self._gateway.transaction(self._tenant_id) as tx:
            row = await tx.fetchrow(self._sql.unchanged_hash_sql, self._tenant_id, file_hash)
            return row["doc_id"] if row is not None else None

    async def claim_document(
        self, doc_id: str, project_id: str, source_project_id: str | None
    ) -> bool:
        """Insert one project_claims row (W4); True only when it was new.

        The dedup skip branch's write: membership rides the claims table.
        """
        async with self._gateway.transaction(self._tenant_id) as tx:
            row = await tx.fetchrow(
                _CLAIM_SQL, self._tenant_id, project_id, doc_id, source_project_id
            )
            return row is not None

    async def document_origin_project(self, doc_id: str) -> str | None:
        """The origin project of a document (None when no project is set)."""
        async with self._gateway.transaction(self._tenant_id) as tx:
            return await tx.fetchval(_ORIGIN_SQL, self._tenant_id, doc_id)

    async def count_chunks_for_document(self, doc_id: str) -> int:
        """The document's total live chunk count in the store.

        Tombstoned rows stay out: they are leftovers of an earlier, larger
        chunking. Unlike ``store_chunks`` (new and rewritten rows only),
        this count answers the full stored number, so an idempotent re-run
        still reports the document's real size (issue #222).
        """
        async with self._gateway.transaction(self._tenant_id) as tx:
            count = await tx.fetchval(_CHUNK_COUNT_SQL, self._tenant_id, doc_id)
        return int(count or 0)

    async def begin_run(self, document: Document, file_hash: str) -> tuple[str, str]:
        """Upsert the document row and open the 'running' ledger row, atomically.

        Runs FIRST in the pipeline, before any chunk/vector/extraction work.
        Returns (doc_id, run_id). Postgres upserts the document row; DuckDB
        only INSERTS (a referenced row cannot be rewritten), so the stored
        doc_id is fetched and run freshness moves to the ledger (030).
        """
        async with self._gateway.transaction(self._tenant_id) as tx:
            row = await tx.fetchrow(
                self._sql.document_insert_sql,
                document.document_id,
                self._tenant_id,
                document.path,
                document.source_type,
                document.chunk_count,
                document.size_bytes,
                file_hash,
                json.dumps(document.metadata),
                _document_date_param(document),
                _document_title_param(document),
            )
            if row is None:
                doc_id = await tx.fetchval(
                    self._sql.document_fetch_sql, self._tenant_id, document.path
                )
            else:
                doc_id = row["doc_id"]
            run_id = await tx.fetchval(
                self._sql.run_insert_sql,
                self._tenant_id,
                *(
                    (document.path, document.source_type, file_hash)
                    if self._sql.is_duckdb
                    else (document.source_type, file_hash)
                ),
            )
        return doc_id, run_id

    async def update_document(self, document: Document, file_hash: str) -> None:
        """Refresh the document row at run end (Postgres only).

        DuckDB rows are immutable once referenced, so this is a no-op there;
        run freshness lives in the ``ingest_runs`` ledger (migration 030).
        """
        if self._sql.is_duckdb:
            logger.debug("update_document skipped: DuckDB documents rows are immutable")
            return
        async with self._gateway.transaction(self._tenant_id) as tx:
            await tx.execute(
                self._sql.document_insert_sql,
                document.document_id,
                self._tenant_id,
                document.path,
                document.source_type,
                document.chunk_count,
                document.size_bytes,
                file_hash,
                json.dumps(document.metadata),
                _document_date_param(document),
                _document_title_param(document),
            )

    async def finish_run(
        self,
        run_id: str,
        file_hash: str,
        stages: dict[str, Any],
        errors: list[str],
    ) -> None:
        """Close the ledger row LAST.

        Empty error list -> completed with finish_hash + the stages summary;
        any error -> failed with the joined error text and no finish_hash.
        """
        completed = not errors
        async with self._gateway.transaction(self._tenant_id) as tx:
            await tx.execute(
                self._sql.finish_run_sql,
                run_id,
                "completed" if completed else "failed",
                file_hash if completed else None,
                json.dumps(stages),
                None if completed else "; ".join(errors),
                self._tenant_id,
            )

    async def fail_run(self, run_id: str, error: str) -> None:
        """Mark the run failed from an exception path. Best effort: never raises."""
        try:
            async with self._gateway.transaction(self._tenant_id) as tx:
                await tx.execute(
                    self._sql.fail_run_sql,
                    run_id,
                    error,
                    self._tenant_id,
                )
        except Exception as exc:
            logger.warning("Could not mark ingest run %s failed: %s", run_id, exc)

    async def set_run_project(self, run_id: str, project_id: str) -> None:
        """Attach a project to a ledger run (migration 036). Best effort: never raises.

        A run row that never existed matches zero rows; a missing column or
        an unreachable database is a warning, never an ingest failure.
        """
        try:
            async with self._gateway.transaction(self._tenant_id) as tx:
                await tx.execute(
                    self._sql.set_run_project_sql,
                    run_id,
                    project_id,
                    self._tenant_id,
                )
        except Exception as exc:
            logger.warning(
                "Could not link ingest run %s to project %s: %s", run_id, project_id, exc
            )

    async def set_document_project(self, doc_id: str, project_id: str) -> None:
        """Attach a project to the document row (migration 016). Best effort: never raises.

        Mirrors ``set_run_project``: a missing row or database is a warning,
        never an ingest failure.
        """
        try:
            async with self._gateway.transaction(self._tenant_id) as tx:
                await tx.execute(
                    self._sql.set_document_project_sql,
                    doc_id,
                    project_id,
                    self._tenant_id,
                )
        except Exception as exc:
            logger.warning("Could not link document %s to project %s: %s", doc_id, project_id, exc)

    async def store_chunks(self, chunks: list[Chunk]) -> set[str]:
        """Insert/update/tombstone chunks. Returns chunk_ids needing (re-)embedding.

        Postgres writes set-based (``ingest_batch``); DuckDB keeps the
        per-row loop. DuckDB pre-pass: the engine's FK checks consult the
        last COMMITTED state, so the derived dependents of every rewritten
        chunk are deleted AND committed in their own scope BEFORE the
        rewrites land; all deleted rows are derived/replaceable, and a
        changed chunk is re-embedded right after.
        """
        if not chunks:
            return set()
        if self._sql.is_duckdb:
            return await store_chunks_duckdb(self._gateway, self._tenant_id, chunks)
        return await self._store_chunks_pg(chunks)

    async def _store_chunks_pg(self, chunks: list[Chunk]) -> set[str]:
        """Set-based chunk write path (Postgres): probe once, then batch."""
        needs_embedding: set[str] = set()
        doc_id = chunks[0].document_id
        indexed = [
            (chunk, chunk.sibling_order if chunk.sibling_order is not None else count)
            for count, chunk in enumerate(chunks)
        ]
        async with self._gateway.transaction(self._tenant_id) as tx:
            existing = await ingest_batch.fetch_chunk_state(tx, self._tenant_id, doc_id)
            inserts = [pair for pair in indexed if pair[1] not in existing]
            updates = [
                (chunk, existing[index][0])
                for chunk, index in indexed
                if index in existing and existing[index][1] != chunk.chunk_hash
            ]
            needs_embedding |= await ingest_batch.insert_chunks(
                tx, self._tenant_id, doc_id, inserts
            )
            needs_embedding |= await ingest_batch.update_chunks(tx, updates)
            # An existing row keeps its stored id (inserts are skipped, not
            # rewritten), so the in-memory chunk must adopt it: later stages
            # (relations, statements) key on chunk.chunk_id, and a resumed
            # ingest minting fresh uuid4 ids would otherwise dangle. The
            # DuckDB loop applies the same re-point.
            for chunk, index in indexed:
                if index in existing:
                    chunk.chunk_id = existing[index][0]
            await tx.execute(_CHUNK_TOMBSTONE_SQL, self._tenant_id, doc_id, len(chunks))
        return needs_embedding

    async def store_vectors(
        self,
        chunks: list[Chunk],
        embedding_model: str,
        dimensions: int = EMBEDDING_DIMENSIONS,
        vectors_1024: dict[str, list[float]] | None = None,
    ) -> int:
        """Insert chunk vectors; the full contract lives on
        ingest_store_chunks.store_vectors (overwrite + honest degraded flag).
        """
        if not chunks:
            return 0
        v1024 = vectors_1024 or {}
        rows = [
            vector_row(self._tenant_id, chunk, embedding_model, dimensions, v1024)
            for chunk in chunks
            if chunk.embedding is not None
        ]
        return await _store_vector_rows(self._gateway, self._tenant_id, rows)

    async def store_entities(self, entities: list[Entity]) -> dict[str, str]:
        """Insert entities; returns ``{extraction_id: canonical_id}``.

        The (tenant_id, name, entity_type) dedup keeps the FIRST stored row
        for a known entity, so relations must point at the canonical id,
        never the extraction-time one. Provenance (no confidence/extractor
        columns on entities, migration 008) merges into the persisted
        metadata JSONB at this boundary.
        """
        id_map: dict[str, str] = {}
        if not entities:
            return id_map
        if not self._sql.is_duckdb:
            async with self._gateway.transaction(self._tenant_id) as tx:
                return await ingest_batch.upsert_entities(tx, self._tenant_id, entities)
        async with self._gateway.transaction(self._tenant_id) as tx:
            for entity in entities:
                row = await tx.fetchrow(
                    self._sql.entity_insert_sql,
                    entity.entity_id,
                    self._tenant_id,
                    entity.name,
                    entity.entity_type,
                    entity.source_document_id,
                    entity_persisted_metadata(entity),
                )
                if row is None:
                    # DuckDB dedupe: relations reference entities, so the
                    # known row is FETCHED, never rewritten. Same contract
                    # as the Postgres no-op update: the canonical id wins.
                    row = await tx.fetchrow(
                        self._sql.entity_fetch_sql,
                        self._tenant_id,
                        entity.name,
                        entity.entity_type,
                    )
                if row is not None:
                    id_map[entity.entity_id] = str(row["entity_id"])
        return id_map

    async def store_relations(
        self,
        relations: list[Relation],
        model_version: str | None = None,
        prompt_version: str | None = None,
    ) -> int:
        """Insert relations into the relations table. Returns count.

        model_version/prompt_version label the extractor configuration that
        produced them; None for relations with no extractor provenance.
        """
        if not relations:
            return 0
        if not self._sql.is_duckdb:
            async with self._gateway.transaction(self._tenant_id) as tx:
                return await ingest_batch.insert_relations(
                    tx, self._tenant_id, relations, model_version, prompt_version
                )
        count = 0
        async with self._gateway.transaction(self._tenant_id) as tx:
            for relation in relations:
                row = await tx.fetchrow(
                    """
                    INSERT INTO relations (relation_id, tenant_id, source_entity_id,
                        target_entity_id, relation_type, weight, metadata,
                        chunk_id, confidence, extractor_id, model_version, prompt_version)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                    ON CONFLICT (tenant_id, source_entity_id, target_entity_id,
                        relation_type, chunk_id)
                    DO NOTHING
                    RETURNING relation_id
                    """,
                    relation.relation_id,
                    self._tenant_id,
                    relation.source_entity_id,
                    relation.target_entity_id,
                    relation.relation_type,
                    relation.weight if relation.weight else 1.0,
                    json.dumps(relation.metadata),
                    relation.chunk_id,
                    relation.confidence,
                    relation.extractor_id,
                    model_version,
                    prompt_version,
                )
                if row is not None:
                    count += 1
        return count
