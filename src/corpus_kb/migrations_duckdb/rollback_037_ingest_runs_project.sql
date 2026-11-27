-- Rollback of 037_ingest_runs_project: drop the nullable project link.
--
-- DuckDB refuses any DROP COLUMN on a table that carries an index, so
-- every ingest_runs index leaves first; the four pre-037 indexes return
-- after the column drops (issue #592 item 1 pin surfaced this).

DROP INDEX IF EXISTS idx_ingest_runs_incomplete;
DROP INDEX IF EXISTS idx_ingest_runs_tenant;
DROP INDEX IF EXISTS idx_ingest_runs_tenant_project;
DROP INDEX IF EXISTS idx_ingest_runs_tenant_source_recent;
DROP INDEX IF EXISTS idx_ingest_runs_tenant_start_hash;

ALTER TABLE ingest_runs DROP COLUMN IF EXISTS project_id;

CREATE INDEX IF NOT EXISTS idx_ingest_runs_tenant ON ingest_runs(tenant_id);

CREATE INDEX IF NOT EXISTS idx_ingest_runs_tenant_start_hash
    ON ingest_runs(tenant_id, start_hash, status);

CREATE INDEX IF NOT EXISTS idx_ingest_runs_incomplete
    ON ingest_runs(tenant_id, started_at);

CREATE INDEX IF NOT EXISTS idx_ingest_runs_tenant_source_recent
    ON ingest_runs(tenant_id, source, started_at);
