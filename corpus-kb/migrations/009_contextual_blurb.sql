-- ============================================================================
-- Migration 009: contextual retrieval blurb
-- ============================================================================
-- Adds chunks.context_blurb (nullable) and a contextual full-text index.
-- The existing idx_chunks_fts (text-only) is left in place; the query
-- layer switches to the contextual expression in this same release, so
-- the old index becomes unused but harmless (can be dropped in a later
-- cleanup migration once contextual retrieval has baked).

SET app.current_tenant_id = '00000000-0000-0000-0000-000000000001';

ALTER TABLE chunks ADD COLUMN IF NOT EXISTS context_blurb TEXT;

DO $$
BEGIN
  EXECUTE 'CREATE INDEX IF NOT EXISTS idx_chunks_fts_contextual ON chunks USING gin '
          '(to_tsvector(''english'', coalesce(context_blurb,'''') || '' '' || text))';
EXCEPTION WHEN OTHERS THEN
  RAISE WARNING 'Skipping idx_chunks_fts_contextual: %', SQLERRM;
END $$;
