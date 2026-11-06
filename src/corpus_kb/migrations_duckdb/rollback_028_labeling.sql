-- ============================================================================
-- Rollback migration 028 (DuckDB): drop the weak-supervision labeling layer.
-- ============================================================================
-- Pair of migrations/rollback_028_labeling.sql: the derived artifact drops
-- first, then the authored input. A re-run of labeling_build rebuilds both.
-- ============================================================================

DROP INDEX IF EXISTS idx_chunk_labels_tenant_label;
DROP INDEX IF EXISTS idx_label_functions_tenant;

DROP TABLE IF EXISTS chunk_labels;
DROP TABLE IF EXISTS label_functions;
