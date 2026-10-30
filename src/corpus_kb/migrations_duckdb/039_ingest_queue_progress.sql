-- ============================================================================
-- Migration 039: the ingest queue progress counters (DuckDB mirror)
-- ============================================================================
-- Pair of migrations/038_ingest_queue_progress.sql. Translation rules
-- (000 header): UUID -> VARCHAR, JSONB -> JSON; RLS stays dropped (the
-- gateway carries tenant predicates). INTEGER maps 1:1.
--
-- DuckDB is at 039 because 030 is its own ledger migration; see that
-- file for the numbering note.
-- Idempotent: ADD COLUMN IF NOT EXISTS.
-- ============================================================================

ALTER TABLE ingest_queue ADD COLUMN IF NOT EXISTS pages_done INTEGER;

ALTER TABLE ingest_queue ADD COLUMN IF NOT EXISTS pages_total INTEGER;

ALTER TABLE ingest_queue ADD COLUMN IF NOT EXISTS chunks INTEGER;
