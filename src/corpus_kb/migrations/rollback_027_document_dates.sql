-- ============================================================================
-- Rollback migration 027: drop the document date provenance column.
-- ============================================================================
-- One derived column plus its index; nothing else was created (no tables,
-- no functions, no events). The backfill source data (chunks.metadata,
-- chunks.source_timestamp) is untouched by design; a re-run of migration
-- 027 recomputes the dates. The migration can be re-applied afterwards
-- (ADD COLUMN IF NOT EXISTS / IF NOT EXISTS index).
-- ============================================================================

SET app.current_tenant_id = '00000000-0000-0000-0000-000000000001';

DROP INDEX IF EXISTS idx_documents_tenant_date;

ALTER TABLE documents DROP COLUMN IF EXISTS document_date;
