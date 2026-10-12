-- ============================================================================
-- Rollback migration 021: drop the U48/P6 shadow hierarchy index.
-- ============================================================================
-- Pure derived artifact: nothing outside the two shadow tables was created
-- (no functions, no events, no v1 changes), so the rollback is two ordered
-- drops. v1 chunks/chunks_vectors are untouched by design. The migration can
-- be re-applied afterwards (CREATE TABLE IF NOT EXISTS / IF NOT EXISTS
-- indexes / DROP POLICY IF EXISTS policies).
-- ============================================================================

SET app.current_tenant_id = '00000000-0000-0000-0000-000000000001';

DROP INDEX IF EXISTS idx_shadow_vectors_hnsw_halfvec;

DROP TABLE IF EXISTS shadow_vectors;
DROP TABLE IF EXISTS shadow_chunks;
