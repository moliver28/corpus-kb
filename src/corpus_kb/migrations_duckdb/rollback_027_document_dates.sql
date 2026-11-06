-- ============================================================================
-- Rollback migration 027 (DuckDB): drop the document date column.
-- ============================================================================
-- Pair of migrations/rollback_027_document_dates.sql: index first, then the
-- column. The ingest write path recomputes document_date on every upsert.
-- ============================================================================

DROP INDEX IF EXISTS idx_documents_tenant_date;

ALTER TABLE documents DROP COLUMN IF EXISTS document_date;
