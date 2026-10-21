-- ============================================================================
-- Migration 024: rrf_fusion deterministic tie order (DuckDB — dropped)
-- ============================================================================
-- Pair of migrations/024_rrf_fusion_tiebreak.sql. It replaces the
-- corpus.rrf_fusion body so fused ties order by chunk id ASC (total order:
-- fused score DESC, then chunk_id ASC). No SQL function exists on DuckDB;
-- handlers/query_fusion.py:rrf_fuse() implements the same total order there
-- and the handler re-asserts it. No statement runs here; the file keeps
-- the 1:1 numbering.
-- ============================================================================

SELECT 1 WHERE FALSE;
