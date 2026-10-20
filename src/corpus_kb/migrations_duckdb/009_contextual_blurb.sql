-- ============================================================================
-- Migration 009: contextual retrieval blurb (DuckDB mirror)
-- ============================================================================
-- Pair of migrations/009_contextual_blurb.sql. Adds chunks.context_blurb.
-- The Postgres GIN index over to_tsvector('english',
-- coalesce(context_blurb,'') || ' ' || text) is dropped: DuckDB full-text
-- search uses the fts extension index over plain text, owned by the FTS
-- manager at runtime.
-- ============================================================================

ALTER TABLE chunks ADD COLUMN IF NOT EXISTS context_blurb TEXT;
