-- ============================================================================
-- Rollback migration 039: drop the ingest queue progress counters.
-- ============================================================================
-- Three derived columns; no tables, no indexes, no functions. The worker
-- recomputes them on the next run. The migration can be re-applied
-- afterwards (ADD COLUMN IF NOT EXISTS).
-- ============================================================================

ALTER TABLE ingest_queue DROP COLUMN IF EXISTS pages_done;

ALTER TABLE ingest_queue DROP COLUMN IF EXISTS pages_total;

ALTER TABLE ingest_queue DROP COLUMN IF EXISTS chunks;
