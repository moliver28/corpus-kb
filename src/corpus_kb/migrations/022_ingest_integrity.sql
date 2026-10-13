-- ============================================================================
-- Migration 022: Ingest integrity — ingest_runs ledger + degraded flags
-- ============================================================================
-- The direct-write ingest path (src/tools/ingest_common.py) previously had no
-- run ledger and no honesty about embedder failures:
--
--   * The dedupe short-circuit probed `documents.file_hash` presence only, so
--     a CRASHED or partially-failed ingest was enshrined as success: every
--     later ingest of the same content skipped forever. `ingest_runs` is the
--     per-run ledger the pipeline now opens (status 'running', start_hash)
--     before any stage writes and closes (completed/failed, finish_hash,
--     stages summary) after the last one. The short-circuit becomes "a
--     COMPLETED run whose stages->'errors' is empty".
--   * `chunks_vectors.vector` rows written from an embedder outage are
--     all-zero poison markers. `chunks_vectors.degraded` / `chunks.degraded`
--     record that honestly per row (query layers decide policy; migration
--     023+ / the search layer consume it), and existing zero-vector rows are
--     backfilled below.
--
-- RLS mirrors the 016/019/020/021 convention: ENABLE + DROP POLICY IF EXISTS
-- + tenant GUC policy + FORCE. The zero-vector backfill runs under a
-- TRANSIENT NO FORCE RLS window (011 FORCEd these tables; a migration-scoped
-- UPDATE would otherwise only see rows of the GUC's single tenant) and
-- re-FORCEs immediately after — same owner-connection pattern the rest of
-- this file uses.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS, ADD COLUMN IF NOT EXISTS,
-- CREATE INDEX IF NOT EXISTS, DROP POLICY IF EXISTS, and a backfill guarded
-- by `WHERE degraded = FALSE` (run-twice is a no-op).
-- ============================================================================

SET app.current_tenant_id = '00000000-0000-0000-0000-000000000001';

-- ----------------------------------------------------------------------------
-- 1. ingest_runs — the ingest integrity ledger
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ingest_runs (
    run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    source_type TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    start_hash TEXT,
    finish_hash TEXT,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running','completed','failed')),
    stages JSONB NOT NULL DEFAULT '{}',
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_ingest_runs_tenant ON ingest_runs(tenant_id);

-- Dedupe probe path: completed clean runs for a content hash.
CREATE INDEX IF NOT EXISTS idx_ingest_runs_tenant_start_hash
    ON ingest_runs(tenant_id, start_hash, status);

-- Doctor/watch surface (Stage: ingest front door): runs that never closed.
CREATE INDEX IF NOT EXISTS idx_ingest_runs_incomplete
    ON ingest_runs(tenant_id, started_at) WHERE status = 'running';

-- ----------------------------------------------------------------------------
-- 2. degraded flags on chunks + chunks_vectors
-- ----------------------------------------------------------------------------

ALTER TABLE chunks ADD COLUMN IF NOT EXISTS degraded BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE chunks_vectors ADD COLUMN IF NOT EXISTS degraded BOOLEAN NOT NULL DEFAULT FALSE;

-- Reembed/audit scan: find the poisoned rows without touching healthy ones.
CREATE INDEX IF NOT EXISTS idx_chunks_vectors_degraded
    ON chunks_vectors(tenant_id) WHERE degraded;

-- ----------------------------------------------------------------------------
-- 3. RLS: tenant isolation on the ledger (migration 016 policy pattern,
--    ENABLE + FORCE, DROP POLICY IF EXISTS for re-apply)
-- ----------------------------------------------------------------------------

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['ingest_runs'] LOOP
        BEGIN
            EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
            EXECUTE format('DROP POLICY IF EXISTS %I_tenant_isolation ON %I', t, t);
            EXECUTE format(
                'CREATE POLICY %I_tenant_isolation ON %I '
                'USING (tenant_id = NULLIF(current_setting(''app.current_tenant_id'', true), '''')::UUID) '
                'WITH CHECK (tenant_id = NULLIF(current_setting(''app.current_tenant_id'', true), '''')::UUID)',
                t, t);
            EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING 'RLS setup failed for %: %', t, SQLERRM;
        END;
    END LOOP;
END $$;

-- ----------------------------------------------------------------------------
-- 4. Backfill: existing zero-vector rows are embedder-failure poison.
-- TRANSIENT NO FORCE RLS: 011 FORCEd chunks/chunks_vectors; the owner
-- connection must bypass RLS for the window so EVERY tenant's rows are
-- covered, not just the GUC tenant. FORCE is restored immediately after.
-- The zero-vector test is l2_distance(vector, zero) = 0 with the zero vector
-- built per-row from vector_dims() — exact float zeros give exactly 0.
--
-- The window blocks RE-RAISE on failure (no RAISE WARNING swallow): the
-- migration runner wraps this file in one transaction, so a failed NO
-- FORCE/FORCE aborts the run and is retried cleanly instead of recording a
-- partial backfill as applied while RLS stayed forced (which would leave
-- every non-GUC tenant's poison rows unflagged and invisible to repair).
-- ----------------------------------------------------------------------------

DO $$
BEGIN
    ALTER TABLE chunks_vectors NO FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE;
END $$;

DO $$
BEGIN
    ALTER TABLE chunks NO FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE;
END $$;

UPDATE chunks_vectors SET degraded = TRUE
WHERE degraded = FALSE
  AND vector IS NOT NULL
  AND l2_distance(
      vector,
      ('[' || array_to_string(array_fill(0::float8, ARRAY[vector_dims(vector)]), ',') || ']')::vector
  ) = 0;

-- Mirror the flag onto the owning chunk rows (write-path keeps them in sync).
UPDATE chunks SET degraded = TRUE
FROM chunks_vectors v
WHERE chunks.chunk_id = v.chunk_id
  AND v.degraded
  AND chunks.degraded = FALSE;

DO $$
BEGIN
    ALTER TABLE chunks_vectors FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE;
END $$;

DO $$
BEGIN
    ALTER TABLE chunks FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE;
END $$;
