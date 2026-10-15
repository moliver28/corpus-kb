-- ============================================================================
-- Rollback migration 026: drop the Wave 4 topic layer.
-- ============================================================================
-- Pure derived artifact: nothing outside the two topic tables was created
-- (no functions, no events, no v1 changes), so the rollback is two ordered
-- drops (assignments first, then topics). chunks/chunks_vectors are
-- untouched by design; a re-run of topics_build rebuilds everything. The
-- migration can be re-applied afterwards (CREATE TABLE IF NOT EXISTS /
-- IF NOT EXISTS indexes / DROP POLICY IF EXISTS policies).
-- ============================================================================

SET app.current_tenant_id = '00000000-0000-0000-0000-000000000001';

DROP INDEX IF EXISTS idx_chunk_topics_topic;
DROP INDEX IF EXISTS idx_topics_tenant_size;

DROP TABLE IF EXISTS chunk_topics;
DROP TABLE IF EXISTS topics;
