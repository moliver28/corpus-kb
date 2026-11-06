-- ============================================================================
-- Rollback migration 021 (DuckDB): drop the shadow hierarchy tables.
-- ============================================================================
-- Pair of migrations/rollback_021_shadow_vectors.sql. The HNSW index never
-- existed on DuckDB (exact scan), so only the two tables drop. Re-applying
-- 021 recreates them (CREATE TABLE IF NOT EXISTS).
-- ============================================================================

DROP TABLE IF EXISTS shadow_vectors;
DROP TABLE IF EXISTS shadow_chunks;
