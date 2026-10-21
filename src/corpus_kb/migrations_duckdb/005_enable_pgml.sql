-- ============================================================================
-- Migration 005: PostgresML extension (DuckDB mirror — dropped by design)
-- ============================================================================
-- Pair of migrations/005_enable_pgml.sql. It loads pgml for in-database
-- embedding, reranking, and extraction. PostgresML runs only on Postgres;
-- on DuckDB the Ollama embedder and the configured non-pgml extractor and
-- reranker serve the same operations (config load refuses pgml there).
-- No statement runs here; the file keeps the 1:1 numbering.
-- ============================================================================

SELECT 1 WHERE FALSE;
