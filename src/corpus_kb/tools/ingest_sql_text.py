"""The ingest SQL texts (pure move out of ``ingest_sql.py``, 250-line rule).

Every statement here is byte-identical to its pre-split text; the backend
selection stays in :func:`ingest_sql.ingest_sql_for`. The Postgres
statements are byte-identical to the pre-port SQL text; the DuckDB
statements differ ONLY where the engines differ (the rationale per
difference lives with the ingest_sql module docstring).
"""

from __future__ import annotations

DOCUMENT_UPSERT_SQL = """
    INSERT INTO documents (doc_id, tenant_id, source, source_type,
        chunk_count, file_size, file_hash, metadata, document_date, title)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
    ON CONFLICT (tenant_id, source) DO UPDATE
    SET chunk_count = $5, file_size = $6, file_hash = $7,
        document_date = COALESCE(EXCLUDED.document_date, documents.document_date),
        title = COALESCE(EXCLUDED.title, documents.title),
        updated_at = NOW()
    RETURNING doc_id::text
"""

# DuckDB cannot UPDATE a referenced parent row (an UPDATE rewrites the row,
# and eight child tables reference documents — engine limitation, duckdb#8988),
# so the DuckDB document "upsert" inserts once and afterwards only fetches
# the existing doc_id; run freshness lives in the ledger (migration 030
# adds ingest_runs.source). The title rides the first insert (issue #222):
# a title-less first ingest leaves the column NULL, and DuckDB never
# rewrites the row afterwards anyway.
DOCUMENT_INSERT_DUCKDB_SQL = """
    INSERT INTO documents (doc_id, tenant_id, source, source_type,
        chunk_count, file_size, file_hash, metadata, document_date, title)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
    ON CONFLICT (tenant_id, source) DO NOTHING
    RETURNING doc_id::text
"""

DOCUMENT_FETCH_DUCKDB_SQL = (
    "SELECT doc_id::text FROM documents WHERE tenant_id = $1 AND source = $2"
)

RUN_INSERT_SQL = """
    INSERT INTO ingest_runs (tenant_id, source_type, start_hash, status)
    VALUES ($1, $2, $3, 'running')
    RETURNING run_id::text
"""

RUN_INSERT_DUCKDB_SQL = """
    INSERT INTO ingest_runs (tenant_id, source, source_type, start_hash, status)
    VALUES ($1, $2, $3, $4, 'running')
    RETURNING run_id::text
"""

_ENTITY_UPSERT_SQL = """
    INSERT INTO entities (entity_id, tenant_id, name, entity_type,
        source_document_id, metadata)
    VALUES ($1, $2, $3, $4, $5, $6)
    ON CONFLICT (tenant_id, name, entity_type) DO UPDATE
        SET entity_id = entities.entity_id
    RETURNING entity_id
"""

# DuckDB: an ON CONFLICT DO UPDATE rewrites the row, and relations reference
# entities, so the dedupe inserts-or-fetches instead of rewriting.
_ENTITY_INSERT_DUCKDB_SQL = """
    INSERT INTO entities (entity_id, tenant_id, name, entity_type,
        source_document_id, metadata)
    VALUES ($1, $2, $3, $4, $5, $6)
    ON CONFLICT (tenant_id, name, entity_type) DO NOTHING
    RETURNING entity_id
"""

_ENTITY_FETCH_DUCKDB_SQL = (
    "SELECT entity_id::text AS entity_id FROM entities "
    "WHERE tenant_id = $1 AND name = $2 AND entity_type = $3"
)


PG_VECTOR_TYPEMOD_SQL = (
    "SELECT a.atttypmod AS width, format_type(a.atttypid, a.atttypmod) AS typ "
    "FROM pg_attribute a "
    "WHERE a.attrelid = 'public.chunks_vectors'::regclass AND a.attname = 'embedding'"
)

_UNCHANGED_TAIL_PG = """
    FROM documents d
    WHERE d.tenant_id = $1 AND d.source = $2 AND d.file_hash = $3
      AND EXISTS (
          SELECT 1 FROM ingest_runs r
          WHERE r.tenant_id = d.tenant_id
            AND r.start_hash = d.file_hash
            AND r.status = 'completed'
            AND r.stages->'errors' = '[]'::jsonb
      )
"""

# DuckDB: the documents row is immutable once referenced, so "this content
# is current" means the NEWEST run for this source started with this hash
# and completed clean (migration 030 adds ingest_runs.source).
UNCHANGED_DOC_DUCKDB_SQL = """
    SELECT d.doc_id::text AS doc_id
    FROM documents d
    WHERE d.tenant_id = $1 AND d.source = $2
      AND EXISTS (
          SELECT 1 FROM ingest_runs r
          WHERE r.tenant_id = d.tenant_id
            AND r.source = d.source
            AND r.start_hash = $3
            AND r.status = 'completed'
            AND (r.stages->'errors') = '[]'::JSON
            AND NOT EXISTS (
                SELECT 1 FROM ingest_runs newer
                WHERE newer.tenant_id = r.tenant_id
                  AND newer.source = r.source
                  AND (newer.started_at, newer.run_id) > (r.started_at, r.run_id)
            )
      )
"""

_FINISH_TAIL = """
    UPDATE ingest_runs SET
        status = $2,
        finished_at = NOW(),
        finish_hash = $3,
        stages = {stages_cast},
        error = $5
    WHERE run_id = {run_ref} AND tenant_id = $6
"""

_VECTOR_INSERT = """
    INSERT INTO chunks_vectors (chunk_id, tenant_id, embedding, vector_1024,
        embedding_model, dimensions, degraded)
    VALUES ($1, $2, {embedding}, {v1024}, $5, $6, $7)
    ON CONFLICT (chunk_id) DO UPDATE
    SET embedding = {embedding},
        vector_1024 = {v1024},
        embedding_model = $5, dimensions = $6, embedded_at = NOW(),
        degraded = $7
"""


# --- Reembed repair (the Stage-3 degraded/stale scan + write) ---
COLUMNS_PROBE_PG = (
    "SELECT count(*) FROM information_schema.columns "
    "WHERE table_schema = 'public' AND table_name = ANY($1::text[]) "
    "AND column_name = 'degraded'"
)
COLUMNS_PROBE_DUCKDB = (
    "SELECT count(*) FROM duckdb_columns() WHERE table_name IN "
    "('chunks', 'chunks_vectors') AND column_name = 'degraded'"
)

# Both scan arms carry the same deterministic order (zero-drift rule 9, the
# sp-suite-hardening declared change 2026-10-20): embedded_at with NULLS LAST
# (a stale row with no timestamp sorts last, not first), then the chunk-id
# tiebreaker so same-timestamp batches cannot permute between runs. The PG
# arm previously ordered by embedded_at alone; the DuckDB arm always had the
# tiebreaker. Declared in storage-portability-program.md §16.
SCAN_SQL_PG = """
    SELECT cv.chunk_id::text AS chunk_id, c.doc_id::text AS doc_id,
           c.text, c.context_blurb,
           (cv.embedding IS NULL) AS was_stale
    FROM chunks_vectors cv
    JOIN chunks c ON c.chunk_id = cv.chunk_id
    WHERE cv.tenant_id = $1
      AND (cv.degraded = TRUE OR c.degraded = TRUE OR cv.embedding IS NULL)
      AND ($2::uuid IS NULL OR c.doc_id = $2::uuid)
    ORDER BY cv.embedded_at NULLS LAST, cv.chunk_id ASC
    LIMIT $3
"""
SCAN_SQL_DUCKDB = """
    SELECT cv.chunk_id::text AS chunk_id, c.doc_id::text AS doc_id,
           c.text, c.context_blurb,
           (cv.embedding IS NULL) AS was_stale
    FROM chunks_vectors cv
    JOIN chunks c ON c.chunk_id = cv.chunk_id
    WHERE cv.tenant_id = $1
      AND (cv.degraded = TRUE OR c.degraded = TRUE OR cv.embedding IS NULL)
      AND ($2::VARCHAR IS NULL OR c.doc_id = $2::VARCHAR)
    ORDER BY cv.embedded_at NULLS LAST, cv.chunk_id ASC
    LIMIT $3
"""

REPAIR_SQL_PG = """
    UPDATE chunks_vectors
    SET embedding = $2::halfvec,
        vector_1024 = COALESCE($3::vector, chunks_vectors.vector_1024),
        embedding_model = $4, dimensions = $5,
        embedded_at = NOW(), degraded = FALSE
    WHERE chunk_id = $1::uuid AND tenant_id = $6
"""
REPAIR_SQL_DUCKDB = """
    UPDATE chunks_vectors
    SET embedding = $2::FLOAT[2560],
        vector_1024 = COALESCE(CAST($3 AS FLOAT[1024]), chunks_vectors.vector_1024),
        embedding_model = $4, dimensions = $5,
        embedded_at = NOW(), degraded = FALSE
    WHERE chunk_id = $1 AND tenant_id = $6
"""
DEGRADED_CLEAR_PG = (
    "UPDATE chunks SET degraded = FALSE WHERE chunk_id = $1::uuid AND tenant_id = $2"
)
DEGRADED_CLEAR_DUCKDB = "UPDATE chunks SET degraded = FALSE WHERE chunk_id = $1 AND tenant_id = $2"
