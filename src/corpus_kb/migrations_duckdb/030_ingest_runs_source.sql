-- ============================================================================
-- Migration 030: source column on the ingest ledger (DuckDB only)
-- ============================================================================
-- The DuckDB documents row is FK-referenced by eight child tables, and
-- DuckDB rewrites a row on every UPDATE (delete + append), so a referenced
-- parent row can never be updated (engine limitation, duckdb#8988). The
-- ingest write path therefore leaves the documents row untouched after its
-- first insert on DuckDB and keeps the run freshness in the LEDGER instead:
-- the dedupe probe reads "the newest run for this SOURCE" off ingest_runs.
-- Postgres keeps the pre-port probe (documents.file_hash refreshed per
-- upsert) and does not need this column; ingest_runs on Postgres has no
-- source column and migration 022 stays untouched.
-- ============================================================================

ALTER TABLE ingest_runs ADD COLUMN IF NOT EXISTS source VARCHAR;

CREATE INDEX IF NOT EXISTS idx_ingest_runs_tenant_source_recent
    ON ingest_runs(tenant_id, source, started_at);
