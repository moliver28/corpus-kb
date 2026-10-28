-- ============================================================================
-- Rollback migration 036: drop the ingest ledger's project link.
-- ============================================================================
-- Pure additive migration: a nullable column and one index, nothing
-- references them. Ordered drops: index first, then the column.
-- ============================================================================

DROP INDEX IF EXISTS idx_ingest_runs_tenant_project;

ALTER TABLE ingest_runs DROP COLUMN IF EXISTS project_id;
