-- ============================================================================
-- Rollback migration 020 (DuckDB): drop the exact result cache.
-- ============================================================================
-- Pair of migrations/rollback_020_exact_result_cache.sql. The cache is a
-- pure derived artifact; the pg trigger and function drops never existed
-- here (the gateway's APPEND_ONLY_TABLES guard owns the rule).
-- ============================================================================

DROP TABLE IF EXISTS research_exact_cache;
