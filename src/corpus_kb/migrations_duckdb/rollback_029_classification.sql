-- ============================================================================
-- Rollback migration 029 (DuckDB): drop the few-shot classification layer.
-- ============================================================================
-- Pair of migrations/rollback_029_classification.sql: the derived artifact
-- drops first, then the seed input. A re-run of classification_build
-- rebuilds both.
-- ============================================================================

DROP INDEX IF EXISTS idx_chunk_predictions_set;
DROP INDEX IF EXISTS idx_label_sets_tenant;

DROP TABLE IF EXISTS chunk_predictions;
DROP TABLE IF EXISTS label_sets;
