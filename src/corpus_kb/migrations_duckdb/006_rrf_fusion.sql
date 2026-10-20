-- ============================================================================
-- Migration 006: rrf_fusion SQL function (DuckDB mirror — dropped by design)
-- ============================================================================
-- Pair of migrations/006_rrf_fusion.sql. It creates corpus.rrf_fusion, a SQL
-- UDF fusing the lexical and vector arms with reciprocal rank fusion. DuckDB
-- cannot create that function (the body is plpgsql-flavored SQL with
-- jsonb_array_elements and DISTINCT ON over jsonb).
--
-- The Python replacement is handlers/query_fusion.py:rrf_fuse(). It returns
-- the same rows with the migration-024 total order (fused score DESC, then
-- chunk_id ASC). The query handler calls it on DuckDB; on Postgres the SQL
-- function stays in charge. No statement runs here; the file keeps the
-- 1:1 numbering.
-- ============================================================================

SELECT 1 WHERE FALSE;
