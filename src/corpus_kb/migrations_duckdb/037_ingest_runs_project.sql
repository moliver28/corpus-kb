-- ============================================================================
-- Migration 037: project link on the ingest ledger (DuckDB mirror)
-- ============================================================================
-- Pair of migrations/036_ingest_runs_project.sql. Same plain nullable
-- column (VARCHAR holds the uuid text on DuckDB) and the same (tenant_id,
-- project_id) index. DuckDB is at 037 because 030 is its own ledger
-- migration (ingest_runs.source); see that file for the numbering note.
-- ============================================================================

ALTER TABLE ingest_runs ADD COLUMN IF NOT EXISTS project_id VARCHAR;

CREATE INDEX IF NOT EXISTS idx_ingest_runs_tenant_project
    ON ingest_runs(tenant_id, project_id);
