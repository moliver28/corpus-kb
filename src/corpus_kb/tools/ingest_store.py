"""Ingest write layer over the SqlGateway (storage portability WP).

Every document/chunk/vector/entity/relation write and the ``ingest_runs``
integrity ledger live here; ``ingest_common`` keeps only orchestration
(partition -> chunk -> embed -> extract -> store) and calls into
:class:`PostgresIngestStore`. One pipeline, two backends: the store takes
an asyncpg pool (wrapped in :class:`~corpus_kb.storage.gateway.PostgresGateway`)
or any ``SqlGateway`` (e.g. ``DuckDbGateway``); backend SQL deltas live in
``ingest_sql.py`` and the Postgres SQL text stays byte-identical.

Ledger semantics (migration 022):

* :meth:`PostgresIngestStore.begin_run` opens a ``status='running'`` row with
  the content hash BEFORE any stage writes.
* :meth:`PostgresIngestStore.finish_run` closes it LAST — completed only when
  the stage error list is empty, failed with the error text otherwise.
* :meth:`PostgresIngestStore.unchanged_doc_id` is the dedupe probe: it skips
  only content whose last run COMPLETED with an empty error list, so a
  crashed or partial ingest can never be enshrined as unchanged.

Every method runs inside ``gateway.transaction(tenant_id)`` — on Postgres
that is the held-connection scope with the tenant GUC (``set_config``
is_local) issued FIRST, before any other statement: a LOCAL GUC issued on
an asyncpg autocommit connection evaporates with its statement, which
would leave every later write GUC-less and rejected on FORCE-RLS tables
(migration 011+). On DuckDB the scope is BEGIN..COMMIT and tenancy rides
on the explicit ``tenant_id`` predicate every statement already carries.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, cast

import asyncpg

from ..analysis.dates import parse_date_value
from ..errors import ConfigError
from ..storage.gateway import DuckDbGateway, PostgresGateway, SqlGateway
from ..utils.models import Chunk, Document, Entity, Relation
from . import ingest_batch
from .ingest_batch import entity_persisted_metadata
from .ingest_sql import (
    duckdb_vector_width_probe,
    ingest_sql_for,
    pg_vector_type_mod_probe,
    vector_width_config_error,
    vector_write_params,
)

logger = logging.getLogger(__name__)

DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"

_CHUNK_TOMBSTONE_SQL = (
    "UPDATE chunks SET tombstoned_at = NOW() WHERE tenant_id = $1 AND doc_id = $2 "
    "AND chunk_index >= $3 AND tombstoned_at IS NULL"
)
_CHUNK_DEGRADED_SQL = "UPDATE chunks SET degraded = $3 WHERE chunk_id = $1 AND tenant_id = $2"
_CHUNK_COUNT_SQL = (
    "SELECT COUNT(*) FROM chunks WHERE tenant_id = $1 AND doc_id = $2 AND tombstoned_at IS NULL"
)

# DuckDB: a chunks row referenced by ANY child row (vectors, relations,
# topics, labels, predictions) cannot be rewritten (an UPDATE rewrites the
# row), so a changed or tombstoned chunk's dependents go first. Every one of
# these child rows is derived/replaceable per the migrations that add them.
_DUCKDB_DEPENDENT_TABLES = (
    "chunks_vectors",
    "relations",
    "chunk_topics",
    "chunk_labels",
    "chunk_predictions",
)


def gateway_backend(gateway: SqlGateway) -> str:
    """The backend a gateway drives: ``duckdb`` for DuckDbGateway, else postgres.

    The gateway IS the backend choice — one object, one truth — so statement
    variants key off the driver type, not a parallel config read.
    """
    return "duckdb" if isinstance(gateway, DuckDbGateway) else "postgres"


def as_gateway(source: asyncpg.Pool | SqlGateway) -> SqlGateway:
    """The one pool/gateway coercion: gateways pass through, pools wrap.

    The Postgres driver is a thin asyncpg wrapper, so a test double with an
    ``acquire()`` flows through it unchanged — historical fake-pool tests
    keep working.
    """
    if isinstance(source, (PostgresGateway, DuckDbGateway)):
        return source
    return PostgresGateway(cast("asyncpg.Pool", source))


async def assert_vector_column_width(
    gw: SqlGateway,
    dimensions: int,
    embedding_model: str,
) -> None:
    """Fail fast when ``dimensions`` cannot enter ``chunks_vectors.embedding``.

    The shipped Postgres schema fixes the column at ``halfvec(2560)`` (the
    native width of the default qwen3-embedding:4b), so a model emitting any
    other width CANNOT ingest. Probe the catalog once and raise a typed
    ConfigError naming the schema reality and the two config keys, BEFORE
    the per-chunk inserts fail mid-transaction. The probe is advisory: an
    unreadable catalog or a missing table defers to the INSERT's own error.
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

        Two conditions must hold together: a documents row for this source
        whose file_hash still matches (the file is unchanged), AND a completed
        ``ingest_runs`` row for that hash whose stages recorded no errors (the
        last ingest of it stored everything). A running, failed, or
        errored-stages past run does NOT count as unchanged — re-ingest heals
        it instead of silently skipping.
        """
        async with self._gateway.transaction(self._tenant_id) as tx:
            row = await tx.fetchrow(
                self._sql.unchanged_doc_sql,
                self._tenant_id,
                source,
                file_hash,
            )
            return row["doc_id"] if row is not None else None

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

        Runs FIRST in the pipeline, before any chunk/vector/extraction work:
        the ledger row is the promise that ``finish_run`` (or ``fail_run``)
        will close it. Returns (doc_id, run_id).

        Postgres upserts the document row (chunk_count/file_hash refresh).
        DuckDB only INSERTS: the row is FK-referenced by child tables and
        DuckDB cannot rewrite a referenced row, so the existing doc_id is
        fetched and run freshness moves to the ledger (migration 030).
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

        On DuckDB the documents row is immutable once child rows reference
        it (the engine rewrites rows on UPDATE), so this is a no-op there:
        run freshness lives in the ``ingest_runs`` ledger (migration 030)
        and the dedupe probe reads it from there.
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
        """Close the ledger row LAST, after every store stage has settled.

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

        Callers pass a run id the ingest chain handed out (``begin_run``); a
        run row that never existed (the research path keeps no corpus ledger
        row) simply matches zero rows. A missing column (migration not yet
        applied) or an unreachable database is a warning, never an ingest
        failure.
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

    async def store_chunks(self, chunks: list[Chunk]) -> set[str]:
        """Insert/update/tombstone chunks. Returns chunk_ids needing (re-)embedding.

        Postgres writes set-based (one statement per partition of work,
        ``ingest_batch``); DuckDB keeps the per-row loop (single-writer
        local engine, plus the pre-rewrite dependent deletes below).

        DuckDB pre-pass: the engine's FK checks consult the last COMMITTED
        state (same-transaction deletes are invisible to them), so the
        derived dependents of every rewritten chunk are deleted AND
        committed in their own scope BEFORE the rewrites land. All deleted
        rows are derived/replaceable (vectors, relations, topics, labels,
        predictions), and a changed chunk is re-embedded right after.
        """
        if not chunks:
            return set()
        if self._sql.is_duckdb:
            return await self._store_chunks_duckdb(chunks)
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

    async def _store_chunks_duckdb(self, chunks: list[Chunk]) -> set[str]:
        """Per-row chunk write path (DuckDB): FK commit-visibility pre-pass."""
        needs_embedding: set[str] = set()
        doc_id = chunks[0].document_id
        async with self._gateway.transaction(self._tenant_id) as tx:
            for count, chunk in enumerate(chunks):
                chunk_index = chunk.sibling_order if chunk.sibling_order is not None else count
                row = await tx.fetchrow(
                    "SELECT chunk_id, chunk_hash FROM chunks "
                    "WHERE tenant_id = $1 AND doc_id = $2 AND chunk_index = $3",
                    self._tenant_id,
                    chunk.document_id,
                    chunk_index,
                )
                if row is not None and row["chunk_hash"] != chunk.chunk_hash:
                    await self._delete_chunk_dependents(tx, "WHERE chunk_id = $1", row["chunk_id"])
            await self._delete_chunk_dependents(
                tx,
                "WHERE tenant_id = $1 AND chunk_id IN (SELECT chunk_id FROM chunks "
                "WHERE tenant_id = $1 AND doc_id = $2 AND chunk_index >= $3)",
                self._tenant_id,
                doc_id,
                len(chunks),
            )
        async with self._gateway.transaction(self._tenant_id) as tx:
            for count, chunk in enumerate(chunks):
                chunk_index = chunk.sibling_order if chunk.sibling_order is not None else count
                existing = await tx.fetchrow(
                    "SELECT chunk_id, chunk_hash FROM chunks "
                    "WHERE tenant_id = $1 AND doc_id = $2 AND chunk_index = $3",
                    self._tenant_id,
                    chunk.document_id,
                    chunk_index,
                )
                if existing is None:
                    row = await tx.fetchrow(
                        """
                        INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index,
                            text, source_type, entity_name, heading_path, file_path,
                            start_line, end_line, metadata, chunk_hash, source_timestamp,
                            context_blurb)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                        ON CONFLICT (tenant_id, doc_id, chunk_index) DO NOTHING
                        RETURNING chunk_id
                        """,
                        chunk.chunk_id,
                        self._tenant_id,
                        chunk.document_id,
                        chunk_index,
                        chunk.text,
                        chunk.source_type,
                        chunk.entity_name,
                        json.dumps(chunk.heading_path) if chunk.heading_path else None,
                        chunk.metadata.get("file_path") if chunk.metadata else None,
                        chunk.start_line,
                        chunk.end_line,
                        json.dumps(chunk.metadata),
                        chunk.chunk_hash,
                        chunk.source_timestamp,
                        chunk.context_blurb,
                    )
                    if row is not None:
                        needs_embedding.add(str(row["chunk_id"]))
                elif existing["chunk_hash"] != chunk.chunk_hash:
                    await tx.execute(
                        """
                        UPDATE chunks SET text = $1, chunk_hash = $2, source_timestamp = $3,
                            context_blurb = $4, metadata = $5, superseded_at = NULL,
                            tombstoned_at = NULL
                        WHERE chunk_id = $6
                        """,
                        chunk.text,
                        chunk.chunk_hash,
                        chunk.source_timestamp,
                        chunk.context_blurb,
                        json.dumps(chunk.metadata),
                        existing["chunk_id"],
                    )
                    chunk.chunk_id = str(existing["chunk_id"])
                    needs_embedding.add(str(existing["chunk_id"]))
                else:
                    # Hash unchanged, no-op. DuckDB: relations built later in
                    # this run carry the in-memory chunk_id, so it is
                    # re-pointed at the stored row (extractors key
                    # relations on chunk.chunk_id).
                    chunk.chunk_id = str(existing["chunk_id"])
            await tx.execute(
                _CHUNK_TOMBSTONE_SQL,
                self._tenant_id,
                chunks[0].document_id if chunks else None,
                len(chunks),
            )
        return needs_embedding

    async def _delete_chunk_dependents(self, tx: SqlGateway, where: str, *args: Any) -> None:
        """Drop a chunk's derived child rows (DuckDB pre-rewrite step)."""
        for table in _DUCKDB_DEPENDENT_TABLES:
            await tx.execute(f"DELETE FROM {table} {where}", *args)

    async def store_vectors(
        self,
        chunks: list[Chunk],
        embedding_model: str,
        dimensions: int = 2560,
        vectors_1024: dict[str, list[float]] | None = None,
    ) -> int:
        """Insert chunk vectors. vectors_1024, if provided, is keyed by chunk_id (str).

        Re-embed OVERWRITES: ``vector_1024`` is replaced, never COALESCEd with
        the stored slice, so a re-embed cannot leave a stale matryoshka slice
        next to a fresh vector. Each write also states the row's ``degraded``
        flag honestly (all-zero vectors are the embedder-failure marker) and
        mirrors it onto the owning chunks row.

        Width guard: the embedding column is probed ONCE up front (Postgres
        catalog typmod, DuckDB ``PRAGMA table_info``) and any width error
        inside the loop is re-raised as the same typed ConfigError — a
        model/dimension mismatch is a config problem, not a write failure.
        """
        if not chunks:
            return 0
        active = [chunk for chunk in chunks if chunk.embedding is not None]
        params, degraded_flags = [], []
        for chunk in active:
            row, degraded = self._vector_row(chunk, embedding_model, dimensions, vectors_1024 or {})
            params.append(row)
            degraded_flags.append(degraded)
        async with self._gateway.transaction(self._tenant_id) as tx:
            await self._probe_vector_width(tx, dimensions, embedding_model)
            try:
                if active:
                    await tx.executemany(self._sql.vector_insert_sql, params)
                    if self._sql.is_duckdb:
                        await tx.executemany(
                            _CHUNK_DEGRADED_SQL,
                            [
                                (chunk.chunk_id, self._tenant_id, degraded)
                                for chunk, degraded in zip(active, degraded_flags, strict=False)
                            ],
                        )
                    else:
                        await ingest_batch.mirror_degraded(
                            tx, [chunk.chunk_id for chunk in active], degraded_flags
                        )
            except ConfigError:
                raise
            except Exception as exc:
                raise vector_width_config_error(exc, embedding_model) from exc
        return len(active)

    async def _probe_vector_width(
        self, tx: SqlGateway, dimensions: int, embedding_model: str
    ) -> None:
        if self._sql.is_duckdb:
            await duckdb_vector_width_probe(tx, dimensions, embedding_model)
        else:
            await pg_vector_type_mod_probe(tx, dimensions, embedding_model)

    def _vector_row(
        self,
        chunk: Chunk,
        embedding_model: str,
        dimensions: int,
        vectors_1024: dict[str, list[float]],
    ) -> tuple[tuple[object, ...], bool]:
        """One chunk's vector bind row: (params, degraded flag)."""
        v1024 = vectors_1024.get(str(chunk.chunk_id))
        degraded = all(v == 0.0 for v in chunk.embedding) if chunk.embedding else True
        assert chunk.embedding is not None
        embedding_param, v1024_param = vector_write_params(chunk.embedding, v1024)
        params = (
            chunk.chunk_id,
            self._tenant_id,
            embedding_param,
            v1024_param,
            embedding_model,
            dimensions,
            degraded,
        )
        return params, degraded

    async def store_entities(self, entities: list[Entity]) -> dict[str, str]:
        """Insert entities into the entities table.

        Returns ``{extraction_id: canonical_id}``: the (tenant_id, name,
        entity_type) dedup keeps the FIRST stored row for a known entity,
        so relations must point at the canonical id, never the extraction-
        time one (an unknown extraction id violates the relations FK on the
        next ingest of a document mentioning a known entity).

        The entities table has no confidence/extractor_id columns
        (migration 008 added them to relations only), so that provenance
        is merged into the persisted metadata JSONB here instead of being
        dropped at the write boundary.
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

        model_version/prompt_version label which extractor configuration
        produced these relations (read from corpus_kb.graph.model_version/
        graph.prompt_version in config by the run_pipeline caller); None
        for relations added through a path with no extractor provenance
        (e.g. the direct add_relation graph-tool API).
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
