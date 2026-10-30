-- Rollback of 038_ingest_queue: drop the ingest queue and its indexes.

DROP INDEX IF EXISTS idx_ingest_queue_tenant_project;
DROP INDEX IF EXISTS idx_ingest_queue_claim;
DROP INDEX IF EXISTS idx_ingest_queue_active_hash;

DROP TABLE IF EXISTS ingest_queue;
