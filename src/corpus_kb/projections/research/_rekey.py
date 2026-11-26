"""Re-key one already-ingested source to its fresh research doc_id.

``research_ingest`` of a source the corpus already ingested used to fail
silently (issue #529): the documents upsert matched the old row on
``(tenant_id, source)`` and kept that row's OLD doc_id, while the new
research events carry a NEW id as the aggregate. The speaker and unit
writes then broke the foreign key to ``documents`` and landed in the
dead-letter queue.

:func:`rekey_ingested_document` makes that re-ingest idempotent. It
reads the existing ``documents`` row for ``(tenant_id, source)``. When
the row is absent or already keyed by the incoming id, nothing changes.
When the row keeps an older id, that row and every child row pointing
at it are deleted, children first and the documents row last, so the
caller's INSERT creates the fresh row under the incoming id.

Engine shapes (both delete children first, the documents row last):

- Postgres: plain deletes on the caller's transaction; the declared
  ``ON DELETE CASCADE`` rules remove the children.
- DuckDB: DuckDB checks foreign keys against the last committed state
  (issue #330), so the projector passes the gateway before its write
  transaction opens, and
  :func:`storage.duckdb_cascade.delete_document_cascade` commits one
  stage per table.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from corpus_kb.projections.research._common import drop_derived_rows
from corpus_kb.storage.duckdb_cascade import delete_document_cascade
from corpus_kb.storage.duckdb_gateway import DuckDbGateway

#: The documents upsert shared by the Ingested/Reingested legs (moved
#: here with the re-key, issue #529). ``{metadata_cast}`` is the
#: dialect's JSON cast for the ``$8`` parameter.
DOCUMENTS_INGEST_SQL = """
                INSERT INTO documents
                (doc_id, tenant_id, source, source_type, file_size, file_hash,
                 language, metadata, project_id, title, source_path, source_hash,
                 parser_name, parser_version, doc_version)
                VALUES ($1, $2, $3, $4, $5, $6, $7, {metadata_cast}, $9, $10, $13, $14, $11, $12, 1)
                ON CONFLICT (tenant_id, source) DO UPDATE SET
                    source_type = EXCLUDED.source_type,
                    file_hash = COALESCE(EXCLUDED.file_hash, documents.file_hash),
                    metadata = EXCLUDED.metadata,
                    project_id = COALESCE(EXCLUDED.project_id, documents.project_id),
                    title = COALESCE(EXCLUDED.title, documents.title),
                    source_hash = COALESCE(EXCLUDED.source_hash, documents.source_hash),
                    parser_name = COALESCE(EXCLUDED.parser_name, documents.parser_name),
                    parser_version = COALESCE(EXCLUDED.parser_version, documents.parser_version),
                    updated_at = NOW()
                """

_SELECT_DOC_ID_SQL = """
                SELECT doc_id FROM documents
                WHERE tenant_id = $1 AND source = $2
                """

_DELETE_DOCUMENT_SQL = """
                DELETE FROM documents
                WHERE tenant_id = $1 AND doc_id = $2
                """


async def rekey_ingested_document(
    conn: Any, tenant_id: UUID, incoming_doc_id: Any, source: str
) -> str:
    """Point one ``(tenant_id, source)`` documents row at the incoming id.

    Returns the effective doc_id for the caller's INSERT, which is the
    incoming id in every case. An absent row or a matching id changes
    nothing. An older id is deleted with its children first, so the
    fresh insert meets no conflict and no orphan.
    """
    incoming = str(incoming_doc_id)
    row = await conn.fetchrow(_SELECT_DOC_ID_SQL, str(tenant_id), source)
    if row is None or str(row["doc_id"]) == incoming:
        return incoming
    old_doc_id = str(row["doc_id"])
    if isinstance(conn, DuckDbGateway):
        # DuckDB reads foreign keys at the last commit (issue #330), so
        # each delete stage commits on its own; the cascade owns that
        # order for the whole child tree.
        await delete_document_cascade(conn, str(tenant_id), old_doc_id)
    else:
        # Postgres clears the research read models first (the shared
        # re-transcription drop) and lets the declared cascades take the
        # rest, the documents row last.
        await drop_derived_rows(conn, tenant_id, UUID(old_doc_id))
        await conn.execute(_DELETE_DOCUMENT_SQL, str(tenant_id), old_doc_id)
    return incoming
