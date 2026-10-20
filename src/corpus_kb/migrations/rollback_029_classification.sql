-- ============================================================================
-- Rollback migration 029: drop the few-shot classification layer.
-- ============================================================================
-- The derived artifact (chunk_predictions) drops first, then the seed
-- input (label_sets). chunks is untouched by design; a re-run of
-- classification_build rebuilds the predictions (the seed sets are
-- re-registered by their owner). The migration can be re-applied
-- afterwards (CREATE TABLE IF NOT EXISTS / IF NOT EXISTS indexes /
-- DROP POLICY IF EXISTS policies).
-- ============================================================================

SET app.current_tenant_id = '00000000-0000-0000-0000-000000000001';

DROP INDEX IF EXISTS idx_chunk_predictions_set;
DROP INDEX IF EXISTS idx_label_sets_tenant;

DROP TABLE IF EXISTS chunk_predictions;
DROP TABLE IF EXISTS label_sets;
