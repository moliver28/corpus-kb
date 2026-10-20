-- ============================================================================
-- Migration 027: document date provenance (WP-1, corpus analysis program)
-- ============================================================================
-- One nullable column on the existing documents table:
--
--   * `documents.document_date` — the best known date for the document,
--     taken from its chunks' metadata (chunk `published_time`, then
--     `modified_time`, then the chunk's own `source_timestamp`). NULL
--     means "no honest date found" — absence stays honest (the topics
--     outlier convention). Consumers: the temporal topic-trends build,
--     which reports undated coverage instead of guessing.
--
-- The backfill runs ONCE at migration time and is idempotent
-- (`IS DISTINCT FROM` guard): a re-apply rewrites nothing.
--
-- MALFORMED-DATE GUARD: web metadata strings are free text. A raw
-- `::timestamptz` cast would abort the whole migration on one bad
-- `published_time` (for example "coming soon" or month 13). The DO block
-- pre-filters with a `^\d{4}-\d{2}-\d{2}` regex AND casts per value
-- under an exception handler, so one malformed string degrades to NULL
-- for that candidate — never a failed migration.
--
-- Determinism: documents are visited in doc_id order, chunks in
-- chunk_index/chunk_id order, and the earliest candidate wins (strict
-- `<` keeps the first on ties). Same corpus in, same date out.
--
-- Index naming mirrors 026 (`idx_<table>_tenant_<key>`).
-- Idempotent: ADD COLUMN IF NOT EXISTS + CREATE INDEX IF NOT EXISTS.
-- ============================================================================

SET app.current_tenant_id = '00000000-0000-0000-0000-000000000001';

ALTER TABLE documents ADD COLUMN IF NOT EXISTS document_date TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_documents_tenant_date
    ON documents(tenant_id, document_date);

-- ----------------------------------------------------------------------------
-- Backfill: per document, the earliest honest date across its chunks.
-- Preference per chunk: metadata published_time, then modified_time,
-- then the chunk's source_timestamp. One malformed string degrades to
-- the next candidate; it never aborts the migration.
-- ----------------------------------------------------------------------------

DO $$
DECLARE
    doc RECORD;
    chunk RECORD;
    candidate TIMESTAMPTZ;
    best TIMESTAMPTZ;
BEGIN
    FOR doc IN SELECT doc_id FROM documents ORDER BY doc_id LOOP
        best := NULL;
        FOR chunk IN
            SELECT c.metadata->>'published_time' AS published_time,
                   c.metadata->>'modified_time' AS modified_time,
                   c.source_timestamp AS source_timestamp
            FROM chunks c
            WHERE c.doc_id = doc.doc_id
            ORDER BY c.chunk_index, c.chunk_id
        LOOP
            candidate := NULL;
            BEGIN
                IF chunk.published_time ~ '^\d{4}-\d{2}-\d{2}' THEN
                    candidate := chunk.published_time::timestamptz;
                END IF;
            EXCEPTION WHEN OTHERS THEN
                candidate := NULL;
            END;
            IF candidate IS NULL THEN
                BEGIN
                    IF chunk.modified_time ~ '^\d{4}-\d{2}-\d{2}' THEN
                        candidate := chunk.modified_time::timestamptz;
                    END IF;
                EXCEPTION WHEN OTHERS THEN
                    candidate := NULL;
                END;
            END IF;
            IF candidate IS NULL THEN
                candidate := chunk.source_timestamp;
            END IF;
            IF candidate IS NOT NULL AND (best IS NULL OR candidate < best) THEN
                best := candidate;
            END IF;
        END LOOP;
        UPDATE documents
        SET document_date = best
        WHERE doc_id = doc.doc_id
          AND document_date IS DISTINCT FROM best;
    END LOOP;
END $$;
