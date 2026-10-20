-- ============================================================================
-- Migration 027: document date provenance (DuckDB mirror)
-- ============================================================================
-- Pair of migrations/027_document_dates.sql: one nullable column on
-- documents plus the tenant-scoped date index. Translation notes:
--   * The Postgres DO-block backfill is dropped: DuckDB has no PL/pgSQL,
--     a fresh install has no rows at migration time, and the ingest write
--     path computes document_date on every upsert (ingest_store.py). An
--     existing Postgres corpus reaches DuckDB through migrate-data, which
--     copies the column after Postgres's own backfill (this migration)
--     has run there.
--   * SET app.current_tenant_id and SET timezone = 'UTC' are dropped:
--     Postgres session GUCs that only the dropped backfill needed.
--   * ADD COLUMN IF NOT EXISTS and the plain index port unchanged.
-- ============================================================================

ALTER TABLE documents ADD COLUMN IF NOT EXISTS document_date TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_documents_tenant_date
    ON documents(tenant_id, document_date);
