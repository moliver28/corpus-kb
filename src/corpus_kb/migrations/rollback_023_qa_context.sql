-- ============================================================================
-- Rollback migration 023: drop chunks.qa_context and its indexes.
-- ============================================================================
-- qa_context is derived, rebuildable data (re-ingesting a transcript
-- recreates it), so the rollback is two ordered index drops + the column
-- drop. Re-applying 023 afterwards restores everything (all statements are
-- IF [NOT] EXISTS / guarded). v1 chunks rows themselves are untouched
-- beyond the derived column.
-- ============================================================================

SET app.current_tenant_id = '00000000-0000-0000-0000-000000000001';

DROP INDEX IF EXISTS idx_chunks_fts_qa_context;
DROP INDEX IF EXISTS idx_chunks_qa_context;

ALTER TABLE chunks DROP COLUMN IF EXISTS qa_context;
