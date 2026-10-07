-- ============================================================================
-- Rollback for migration 017 (inductive engine operational state)
-- ============================================================================
-- Drops the three engine-owned tables. Like 016's rollback, this intentionally
-- KEEPS the run-owned state that cannot be rebuilt from events
-- (research_observations is re-derivable only by re-running the LLM summaries
-- and embeddings, so it is dropped; research_proposed_codes decisions are
-- governance history and survive in the event-sourced CodebookVersion chain
-- regardless). event_store/snapshot_store and 016's tables are NOT touched.
-- ============================================================================

DROP TABLE IF EXISTS research_noise_queue;
DROP TABLE IF EXISTS research_proposed_codes;
DROP TABLE IF EXISTS research_observations;
