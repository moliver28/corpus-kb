-- ============================================================================
-- Rollback migration 023 (DuckDB): drop chunks.qa_context.
-- ============================================================================
-- Pair of migrations/rollback_023_qa_context.sql. The two pg indexes never
-- existed here (the qa_context arm is DuckDB's exact-scan + Python side),
-- so only the derived column drops. Re-applying 023 restores it.
-- ============================================================================

ALTER TABLE chunks DROP COLUMN IF EXISTS qa_context;
