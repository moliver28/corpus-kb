-- ============================================================================
-- Migration 004: Apache AGE graph namespace (DuckDB mirror — dropped by design)
-- ============================================================================
-- Pair of migrations/004_enable_age.sql. It loads the AGE extension and
-- creates the "corpus_kb" openCypher graph. DuckDB has no AGE; the DuckDB
-- graph store answers the same operations with recursive CTEs and fixed
-- joins (program doc section 5), so no graph namespace is created here.
-- No statement runs here; the file keeps the 1:1 numbering.
-- ============================================================================

SELECT 1 WHERE FALSE;
