-- ============================================================================
-- Migration 036: project link on the ingest ledger
-- ============================================================================
-- ingest_runs gains a nullable project_id so a run can state which research
-- project it served (UI SPA program: the Scale ledger filter + honest
-- "no project" history). Convention note: this is a PLAIN nullable column,
-- not an FK — documents.project_id (016) set the same precedent, and the
-- research_projects read model is projection-owned (a run may reference a
-- project whose row the rebuild has not re-created yet).
--
-- No backfill: every pre-036 row predates the link and stays NULL; surfaces
-- render that honestly as "no project".
--
-- Idempotent: ADD COLUMN IF NOT EXISTS + CREATE INDEX IF NOT EXISTS.
-- ============================================================================

ALTER TABLE ingest_runs ADD COLUMN IF NOT EXISTS project_id UUID;

CREATE INDEX IF NOT EXISTS idx_ingest_runs_tenant_project
    ON ingest_runs(tenant_id, project_id);
