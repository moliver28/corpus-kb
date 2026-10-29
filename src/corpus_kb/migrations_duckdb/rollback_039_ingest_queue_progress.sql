-- ============================================================================
-- Rollback migration 040: drop the ingest queue progress counters.
-- ============================================================================
-- Pair of migrations/rollback_038_ingest_queue_progress.sql. Three
-- derived columns; no tables, no indexes. The worker recomputes them on
-- the next run. The migration can be re-applied afterwards.
-- ============================================================================

ALTER TABLE ingest_queue DROP COLUMN IF EXISTS pages_done;

ALTER TABLE ingest_queue DROP COLUMN IF EXISTS pages_total;

ALTER TABLE ingest_queue DROP COLUMN IF EXISTS chunks;
