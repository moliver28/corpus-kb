-- ============================================================================
-- Rollback for migration 020 (provenance-keyed exact result cache)
-- ============================================================================
-- Drops the cache table, its trigger function, and its indexes. The cache is
-- a pure DERIVED artifact (rebuildable by re-running the LLM calls), so a
-- rollback loses nothing but recompute time: no source-of-truth data is
-- touched, and re-applying migration 020 recreates an empty cache.
-- ============================================================================

DROP TRIGGER IF EXISTS trg_exact_cache_no_update ON research_exact_cache;
DROP TRIGGER IF EXISTS trg_exact_cache_no_delete ON research_exact_cache;
DROP FUNCTION IF EXISTS corpus.forbid_exact_cache_mutation();
DROP TABLE IF EXISTS research_exact_cache;
