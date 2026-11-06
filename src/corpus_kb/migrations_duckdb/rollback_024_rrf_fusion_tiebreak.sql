-- ============================================================================
-- Rollback migration 024 (DuckDB): no-op.
-- ============================================================================
-- Pair of migrations/rollback_024_rrf_fusion_tiebreak.sql. The Postgres
-- file restores the migration-006 corpus.rrf_fusion body. No SQL function
-- exists on DuckDB; handlers/query_fusion.py:rrf_fuse() is the fusion, and
-- the deterministic tie order it ships IS the current contract. No
-- statement runs here; the file keeps the 1:1 rollback naming.
-- ============================================================================

SELECT 1 WHERE FALSE;
