-- ============================================================================
-- Rollback migration 017 (DuckDB): drop the inductive engine tables.
-- ============================================================================
-- Pair of migrations/rollback_017_inductive_engine.sql. The same three
-- drops; the run-owned state note carries over unchanged.
-- ============================================================================

DROP TABLE IF EXISTS research_noise_queue;
DROP TABLE IF EXISTS research_proposed_codes;
DROP TABLE IF EXISTS research_observations;
