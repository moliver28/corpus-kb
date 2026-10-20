-- ============================================================================
-- Migration 022: ingest integrity (DuckDB mirror)
-- ============================================================================
-- Pair of migrations/022_ingest_integrity.sql. The ingest_runs ledger plus
-- the degraded flags on chunks and chunks_vectors. Translation notes:
--   * both degraded columns arrive with their tables in 000 (DuckDB cannot
--     ADD COLUMN with NOT NULL, and cannot ALTER NOT NULL or DEFAULT on a
--     table with dependent entries); the pair statements below are no-ops.
--   * The zero-vector backfill DO block is dropped: a fresh install has no
--     rows, and the write path sets the flag per row from then on.
--   * Partial indexes lose their WHERE predicate.
--   * RLS block and the FORCE windows are dropped.
-- ============================================================================

-- 1. ingest_runs: the ingest integrity ledger
CREATE TABLE IF NOT EXISTS ingest_runs (
    run_id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR NOT NULL,
    source_type TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    start_hash TEXT,
    finish_hash TEXT,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running','completed','failed')),
    stages JSON NOT NULL DEFAULT '{}',
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_ingest_runs_tenant ON ingest_runs(tenant_id);

-- Dedupe probe path: completed clean runs for a content hash.
CREATE INDEX IF NOT EXISTS idx_ingest_runs_tenant_start_hash
    ON ingest_runs(tenant_id, start_hash, status);

-- Doctor/watch surface: runs that never closed (Postgres keeps this index
-- partial, WHERE status = 'running').
CREATE INDEX IF NOT EXISTS idx_ingest_runs_incomplete
    ON ingest_runs(tenant_id, started_at);

-- 2. degraded flags on chunks + chunks_vectors
-- Both columns were created with their tables in 000 (DuckDB refuses NOT
-- NULL and DEFAULT ALTERs on tables with dependent entries); these
-- statements are the pair-alignment no-ops.
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS degraded BOOLEAN DEFAULT FALSE;
ALTER TABLE chunks_vectors ADD COLUMN IF NOT EXISTS degraded BOOLEAN DEFAULT FALSE;

-- Reembed/audit scan: find the poisoned rows (Postgres keeps this index
-- partial, WHERE degraded).
CREATE INDEX IF NOT EXISTS idx_chunks_vectors_degraded
    ON chunks_vectors(tenant_id);

-- 3. Mirror the poisoned-row flag onto the owning chunk rows (the fresh
-- install has no rows; the write path keeps the flags in sync afterwards).
UPDATE chunks SET degraded = TRUE
FROM chunks_vectors
WHERE chunks.chunk_id = chunks_vectors.chunk_id
  AND chunks_vectors.degraded
  AND chunks.degraded = FALSE;
