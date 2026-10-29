-- Rollback of 037_ingest_runs_project: drop the nullable project link.

DROP INDEX IF EXISTS idx_ingest_runs_tenant_project;

ALTER TABLE ingest_runs DROP COLUMN IF EXISTS project_id;
