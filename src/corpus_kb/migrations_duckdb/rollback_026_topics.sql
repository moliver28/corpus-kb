-- ============================================================================
-- Rollback migration 026 (DuckDB): drop the topic layer.
-- ============================================================================
-- Pair of migrations/rollback_026_topics.sql: assignments first, then
-- topics. chunks/chunks_vectors are untouched by design.
-- ============================================================================

DROP INDEX IF EXISTS idx_chunk_topics_topic;
DROP INDEX IF EXISTS idx_topics_tenant_size;

DROP TABLE IF EXISTS chunk_topics;
DROP TABLE IF EXISTS topics;
