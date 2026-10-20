-- ============================================================================
-- Rollback migration 028: drop the weak-supervision labeling layer.
-- ============================================================================
-- The derived artifact (chunk_labels) drops first, then the authored
-- input (label_functions). chunks is untouched by design; a re-run of
-- labeling_build rebuilds the labels (the authored LFs are re-registered
-- by their owner). The migration can be re-applied afterwards
-- (CREATE TABLE IF NOT EXISTS / IF NOT EXISTS indexes / DROP POLICY IF
-- EXISTS policies).
-- ============================================================================

SET app.current_tenant_id = '00000000-0000-0000-0000-000000000001';

DROP INDEX IF EXISTS idx_chunk_labels_tenant_label;
DROP INDEX IF EXISTS idx_label_functions_tenant;

DROP TABLE IF EXISTS chunk_labels;
DROP TABLE IF EXISTS label_functions;
