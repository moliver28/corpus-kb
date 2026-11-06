-- ============================================================================
-- Rollback migration 022 (DuckDB): drop the ingest integrity ledger.
-- ============================================================================
-- Pair of migrations/rollback_022_ingest_integrity.sql. Index first, then
-- the degraded columns, then the ledger table — the same order as the
-- Postgres rollback, minus the never-created HNSW-side index.
-- ============================================================================

DROP INDEX IF EXISTS idx_chunks_vectors_degraded;
DROP INDEX IF EXISTS idx_ingest_runs_incomplete;
DROP INDEX IF EXISTS idx_ingest_runs_tenant_start_hash;
DROP INDEX IF EXISTS idx_ingest_runs_tenant;

ALTER TABLE chunks_vectors DROP COLUMN IF EXISTS degraded;
ALTER TABLE chunks DROP COLUMN IF EXISTS degraded;

DROP TABLE IF EXISTS ingest_runs;
