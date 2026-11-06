-- ============================================================================
-- Rollback migration 025 (DuckDB): no-op.
-- ============================================================================
-- Pair of migrations/rollback_025_halfvec_2560.sql. The Postgres file
-- restores the vector(4096) column shape. This mirror pre-applied the
-- end state in 000 (FLOAT[2560] columns, exact scan, no HNSW), so there
-- is no per-file state to revert — 000 always creates the 2560-dim shape.
-- No statement runs here; the file keeps the 1:1 rollback naming.
-- ============================================================================

SELECT 1 WHERE FALSE;
