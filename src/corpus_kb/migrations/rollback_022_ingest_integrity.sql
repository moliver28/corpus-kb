-- ============================================================================
-- Rollback migration 022: drop the ingest integrity ledger + degraded flags.
-- ============================================================================
-- Pure additive migration: ingest_runs is created here (nothing references it
-- by FK), the degraded columns carry no indexes beyond the one dropped below,
-- and chunks/chunks_vectors rows are unchanged otherwise. Dropping the
-- degraded columns discards the per-row embedder-failure flags; a re-apply
-- of 022 re-runs the zero-vector backfill to rebuild them from vector data.
-- Ordered drops: index first, then columns, then the ledger table.
-- ============================================================================

SET app.current_tenant_id = '00000000-0000-0000-0000-000000000001';

DROP INDEX IF EXISTS idx_chunks_vectors_degraded;
DROP INDEX IF EXISTS idx_ingest_runs_incomplete;
DROP INDEX IF EXISTS idx_ingest_runs_tenant_start_hash;
DROP INDEX IF EXISTS idx_ingest_runs_tenant;

ALTER TABLE chunks_vectors DROP COLUMN IF EXISTS degraded;
ALTER TABLE chunks DROP COLUMN IF EXISTS degraded;

DROP TABLE IF EXISTS ingest_runs;
