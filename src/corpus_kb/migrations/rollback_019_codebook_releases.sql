-- ============================================================================
-- Rollback for migration 019 (codebook release read models)
-- ============================================================================
-- Drops the four release tables, their trigger functions, and their indexes.
-- Child tables drop BEFORE codebook_releases (FK ON DELETE CASCADE would
-- handle it, but explicit order keeps the rollback runnable even if the FKs
-- are ever relaxed). The trigger functions are dropped last and only if no
-- trigger still references them.
--
-- The EVENT-SOURCED release history is untouched: releases, gate results, and
-- waivers live in the CodebookVersion event chain (domain/codebook.py), so
-- re-applying 019 and replaying the projection rebuilds the tables. The
-- audit_partitions ledger is sampling OUTPUT re-derivable by re-running the
-- seeded sampler with the recorded seed, so it is dropped too.
-- ============================================================================

DROP TRIGGER IF EXISTS trg_release_waivers_frozen ON release_waivers;
DROP TRIGGER IF EXISTS trg_release_gate_results_frozen ON release_gate_results;
DROP TRIGGER IF EXISTS trg_codebook_releases_immutability ON codebook_releases;

DROP TABLE IF EXISTS release_waivers;
DROP TABLE IF EXISTS release_gate_results;
DROP TABLE IF EXISTS audit_partitions;
DROP TABLE IF EXISTS codebook_releases;

DROP FUNCTION IF EXISTS release_children_immutability_guard();
DROP FUNCTION IF EXISTS codebook_releases_immutability_guard();
