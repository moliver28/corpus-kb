-- ============================================================================
-- Rollback migration 037: drop the ingest queue.
-- ============================================================================
-- Pure additive migration: one table and its indexes, nothing references
-- them. Ordered drops: indexes first, then the table.
-- ============================================================================

DROP INDEX IF EXISTS idx_ingest_queue_tenant_project;
DROP INDEX IF EXISTS idx_ingest_queue_claim;
DROP INDEX IF EXISTS idx_ingest_queue_active_hash;

DROP TABLE IF EXISTS ingest_queue;
