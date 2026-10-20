-- ============================================================================
-- Migration 023: transcript qa_context (DuckDB mirror)
-- ============================================================================
-- Pair of migrations/023_qa_context.sql. Adds chunks.qa_context. The two
-- GIN indexes are dropped: DuckDB has no jsonb GIN, and its full-text
-- search uses the fts extension index owned by the FTS manager (the
-- qa_context_in_fts opt-in rides that rebuild, not a migration).
-- ============================================================================

ALTER TABLE chunks ADD COLUMN IF NOT EXISTS qa_context JSON;
