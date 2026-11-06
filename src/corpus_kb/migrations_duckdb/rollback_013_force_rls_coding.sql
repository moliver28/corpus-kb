-- ============================================================================
-- Rollback migration 013 (DuckDB): no-op.
-- ============================================================================
-- Pair of migrations/rollback_013_force_rls_coding.sql. The Postgres file
-- lifts FORCE ROW LEVEL SECURITY from the twelve coding tables. DuckDB has
-- no row-level security (the gateway carries the tenant predicate per
-- statement), so there is nothing to lift. No statement runs here; the file
-- keeps the 1:1 rollback naming.
-- ============================================================================

SELECT 1 WHERE FALSE;
