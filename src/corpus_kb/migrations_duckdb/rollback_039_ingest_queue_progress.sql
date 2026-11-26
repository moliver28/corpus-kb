-- ============================================================================
-- Rollback migration 039: drop the ingest queue progress counters.
-- ============================================================================
-- Pair of migrations/rollback_038_ingest_queue_progress.sql. Three
-- derived columns; no tables, no indexes. The worker recomputes them on
-- the next run. The migration can be re-applied afterwards.
--
-- DuckDB refuses any DROP COLUMN on a table that carries an index, so
-- the 038 indexes leave first and return after the columns drop
-- (issue #592 item 1 pin surfaced this).
-- ============================================================================

DROP INDEX IF EXISTS idx_ingest_queue_active_hash;
DROP INDEX IF EXISTS idx_ingest_queue_claim;
DROP INDEX IF EXISTS idx_ingest_queue_tenant_project;

ALTER TABLE ingest_queue DROP COLUMN IF EXISTS pages_done;

ALTER TABLE ingest_queue DROP COLUMN IF EXISTS pages_total;

ALTER TABLE ingest_queue DROP COLUMN IF EXISTS chunks;

CREATE UNIQUE INDEX IF NOT EXISTS idx_ingest_queue_active_hash
    ON ingest_queue(tenant_id, content_hash);

CREATE INDEX IF NOT EXISTS idx_ingest_queue_claim
    ON ingest_queue(status, queued_at);

CREATE INDEX IF NOT EXISTS idx_ingest_queue_tenant_project
    ON ingest_queue(tenant_id, project_id);
