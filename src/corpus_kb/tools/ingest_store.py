"""Postgres write layer for the ingest pipeline.

Every document/chunk/vector/entity/relation write and the ``ingest_runs``
integrity ledger live here; ``ingest_common`` keeps only orchestration
(partition -> chunk -> embed -> extract -> store) and calls into
:class:`PostgresIngestStore`.

Ledger semantics (migration 022):

* :meth:`PostgresIngestStore.begin_run` opens a ``status='running'`` row with
  the content hash BEFORE any stage writes.
* :meth:`PostgresIngestStore.finish_run` closes it LAST — completed only when
  the stage error list is empty, failed with the error text otherwise.
* :meth:`PostgresIngestStore.unchanged_doc_id` is the dedupe probe: it skips
  only content whose last run COMPLETED with an empty error list, so a
  crashed or partial ingest can never be enshrined as unchanged.

Every method — ledger and the inherited v1 ``store_chunks``/
``store_vectors``/``store_entities``/``store_relations`` alike — runs inside
an explicit ``conn.transaction()`` with the tenant GUC (``set_config``
is_local) issued FIRST inside it, before any other statement: a LOCAL GUC
issued on an asyncpg autocommit connection evaporates with its statement,
which would leave every later write GUC-less and rejected on FORCE-RLS
tables (migration 011+).
"""

from __future__ import annotations

import json
import logging

import asyncpg

from ..utils.models import Chunk, Document, Entity, Relation

logger = logging.getLogger(__name__)

DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"

_DOCUMENT_UPSERT_SQL = """
    INSERT INTO documents (doc_id, tenant_id, source, source_type,
        chunk_count, file_size, file_hash, metadata)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
    ON CONFLICT (tenant_id, source) DO UPDATE
    SET chunk_count = $5, file_size = $6, file_hash = $7, updated_at = NOW()
    RETURNING doc_id::text
"""


class PostgresIngestStore:
    """Wraps all Postgres write operations for the ingest pipeline.

    All methods use the same asyncpg.Pool and set tenant context via SET LOCAL.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> None:
        self._pool = pool
        self._tenant_id = tenant_id

    async def unchanged_doc_id(self, source: str, file_hash: str) -> str | None:
        """Return the doc_id when this exact content was already ingested cleanly.

        Two conditions must hold together: a documents row for this source
        whose file_hash still matches (the file is unchanged), AND a completed
        ``ingest_runs`` row for that hash whose stages recorded no errors (the
        last ingest of it stored everything). A running, failed, or
        errored-stages past run does NOT count as unchanged — re-ingest heals
        it instead of silently skipping.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                self._tenant_id,
            )
            row = await conn.fetchrow(
                """
                SELECT d.doc_id::text AS doc_id
                FROM documents d
                WHERE d.tenant_id = $1 AND d.source = $2 AND d.file_hash = $3
                  AND EXISTS (
                      SELECT 1 FROM ingest_runs r
                      WHERE r.tenant_id = d.tenant_id
                        AND r.start_hash = d.file_hash
                        AND r.status = 'completed'
                        AND r.stages->'errors' = '[]'::jsonb
                  )
                """,
                self._tenant_id,
                source,
                file_hash,
            )
            return row["doc_id"] if row is not None else None

    async def begin_run(self, document: Document, file_hash: str) -> tuple[str, str]:
        """Upsert the document row and open the 'running' ledger row, atomically.

        Runs FIRST in the pipeline, before any chunk/vector/extraction work:
        the ledger row is the promise that ``finish_run`` (or ``fail_run``)
        will close it. Returns (doc_id, run_id).
        """
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                self._tenant_id,
            )
            row = await conn.fetchrow(
                _DOCUMENT_UPSERT_SQL,
                document.document_id,
                self._tenant_id,
                document.path,
                document.source_type,
                document.chunk_count,
                document.size_bytes,
                file_hash,
                json.dumps(document.metadata),
            )
            doc_id: str = row["doc_id"]
            run_id = await conn.fetchval(
                """
                INSERT INTO ingest_runs (tenant_id, source_type, start_hash, status)
                VALUES ($1, $2, $3, 'running')
                RETURNING run_id::text
                """,
                self._tenant_id,
                document.source_type,
                file_hash,
            )
        return doc_id, run_id

    async def update_document(self, document: Document, file_hash: str) -> None:
        """Re-upsert the document row (chunk_count/file_size refresh at run end)."""
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                self._tenant_id,
            )
            await conn.execute(
                _DOCUMENT_UPSERT_SQL,
                document.document_id,
                self._tenant_id,
                document.path,
                document.source_type,
                document.chunk_count,
                document.size_bytes,
                file_hash,
                json.dumps(document.metadata),
            )

    async def finish_run(
        self,
        run_id: str,
        file_hash: str,
        stages: dict[str, object],
        errors: list[str],
    ) -> None:
        """Close the ledger row LAST, after every store stage has settled.

        Empty error list -> completed with finish_hash + the stages summary;
        any error -> failed with the joined error text and no finish_hash.
        """
        completed = not errors
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                self._tenant_id,
            )
            await conn.execute(
                """
                UPDATE ingest_runs SET
                    status = $2,
                    finished_at = NOW(),
                    finish_hash = $3,
                    stages = $4::jsonb,
                    error = $5
                WHERE run_id = $1::uuid AND tenant_id = $6
                """,
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
            async with self._pool.acquire() as conn, conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.current_tenant_id', $1, true)",
                    self._tenant_id,
                )
                await conn.execute(
                    """
                    UPDATE ingest_runs SET status = 'failed', finished_at = NOW(), error = $2
                    WHERE run_id = $1::uuid AND tenant_id = $3
                    """,
                    run_id,
                    error,
                    self._tenant_id,
                )
        except Exception as exc:
            logger.warning("Could not mark ingest run %s failed: %s", run_id, exc)

    async def store_chunks(self, chunks: list[Chunk]) -> set[str]:
        """Insert/update/tombstone chunks. Returns chunk_ids needing (re-)embedding."""
        if not chunks:
            return set()
        needs_embedding: set[str] = set()
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                self._tenant_id,
            )
            for count, chunk in enumerate(chunks):
                chunk_index = chunk.sibling_order if chunk.sibling_order is not None else count
                existing = await conn.fetchrow(
                    "SELECT chunk_id, chunk_hash FROM chunks "
                    "WHERE tenant_id = $1 AND doc_id = $2 AND chunk_index = $3",
                    self._tenant_id,
                    chunk.document_id,
                    chunk_index,
                )
                if existing is None:
                    row = await conn.fetchrow(
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
                    await conn.execute(
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
                    needs_embedding.add(str(existing["chunk_id"]))
                # else: hash unchanged, no-op -- neither inserted nor re-embedded
            await conn.execute(
                "UPDATE chunks SET tombstoned_at = NOW() WHERE tenant_id = $1 AND doc_id = $2 "
                "AND chunk_index >= $3 AND tombstoned_at IS NULL",
                self._tenant_id,
                chunks[0].document_id if chunks else None,
                len(chunks),
            )
        return needs_embedding

    async def store_vectors(
        self,
        chunks: list[Chunk],
        embedding_model: str,
        dimensions: int = 768,
        vectors_1024: dict[str, list[float]] | None = None,
    ) -> int:
        """Insert chunk vectors. vectors_1024, if provided, is keyed by chunk_id (str).

        Re-embed OVERWRITES: ``vector_1024`` is replaced, never COALESCEd with
        the stored slice, so a re-embed cannot leave a stale matryoshka slice
        next to a fresh vector. Each write also states the row's ``degraded``
        flag honestly (all-zero vectors are the embedder-failure marker) and
        mirrors it onto the owning chunks row.
        """
        if not chunks:
            return 0
        count = 0
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                self._tenant_id,
            )
            for chunk in chunks:
                if chunk.embedding is None:
                    continue
                vector_str = "[" + ",".join(str(v) for v in chunk.embedding) + "]"
                v1024 = (vectors_1024 or {}).get(str(chunk.chunk_id))
                v1024_str = "[" + ",".join(str(v) for v in v1024) + "]" if v1024 else None
                degraded = all(v == 0.0 for v in chunk.embedding)
                await conn.execute(
                    """
                    INSERT INTO chunks_vectors (chunk_id, tenant_id, vector, vector_1024,
                        embedding_model, dimensions, degraded)
                    VALUES ($1, $2, $3::vector, $4::vector, $5, $6, $7)
                    ON CONFLICT (chunk_id) DO UPDATE
                    SET vector = $3::vector,
                        vector_1024 = $4::vector,
                        embedding_model = $5, dimensions = $6, embedded_at = NOW(),
                        degraded = $7
                    """,
                    chunk.chunk_id,
                    self._tenant_id,
                    vector_str,
                    v1024_str,
                    embedding_model,
                    dimensions,
                    degraded,
                )
                await conn.execute(
                    "UPDATE chunks SET degraded = $3 WHERE chunk_id = $1 AND tenant_id = $2",
                    chunk.chunk_id,
                    self._tenant_id,
                    degraded,
                )
                count += 1
        return count

    async def store_entities(self, entities: list[Entity]) -> int:
        """Insert entities into the entities table. Returns inserted count.

        The entities table has no confidence/extractor_id columns
        (migration 008 added them to relations only), so that provenance
        is merged into the persisted metadata JSONB here instead of being
        dropped at the write boundary.
        """
        if not entities:
            return 0
        count = 0
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                self._tenant_id,
            )
            for entity in entities:
                row = await conn.fetchrow(
                    """
                    INSERT INTO entities (entity_id, tenant_id, name, entity_type,
                        source_document_id, metadata)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (tenant_id, name, entity_type) DO NOTHING
                    RETURNING entity_id
                    """,
                    entity.entity_id,
                    self._tenant_id,
                    entity.name,
                    entity.entity_type,
                    entity.source_document_id,
                    _entity_persisted_metadata(entity),
                )
                if row is not None:
                    count += 1
        return count

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
        count = 0
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                self._tenant_id,
            )
            for relation in relations:
                row = await conn.fetchrow(
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


def _entity_persisted_metadata(entity: Entity) -> str:
    """Serialize entity metadata with provenance merged in.

    ``extractor`` and ``confidence`` live on the Entity model but have no
    dedicated columns in the entities table; without this merge they would
    be silently dropped at the write boundary even when the extractor
    measured them.
    """
    persisted: dict[str, object] = dict(entity.metadata)
    if entity.extractor_id and "extractor" not in persisted:
        persisted["extractor"] = entity.extractor_id
    if entity.confidence is not None and "confidence" not in persisted:
        persisted["confidence"] = entity.confidence
    return json.dumps(persisted)
