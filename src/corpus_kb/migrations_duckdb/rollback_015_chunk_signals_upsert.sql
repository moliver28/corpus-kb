-- ============================================================================
-- Rollback migration 015 (DuckDB): no-op.
-- ============================================================================
-- Pair of migrations/rollback_015_chunk_signals_upsert.sql. The Postgres
-- file restores the wide primary key (with detail) and re-creates the
-- append-only trigger. This mirror applied the end state in 012 directly
-- (DuckDB cannot alter a primary key; the gateway's APPEND_ONLY_TABLES
-- guard owns the trigger's rule), so there is nothing to revert per-file.
-- No statement runs here; the file keeps the 1:1 rollback naming.
-- ============================================================================

SELECT 1 WHERE FALSE;
