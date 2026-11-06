-- ============================================================================
-- Rollback migration 019 (DuckDB): drop the codebook release read models.
-- ============================================================================
-- Pair of migrations/rollback_019_codebook_releases.sql. Child tables drop
-- BEFORE codebook_releases (the gateway's APPEND_ONLY_TABLES guard rejects
-- their deletes, so a wrong order would fail typed). The pg trigger and
-- function drops never existed here.
-- ============================================================================

DROP TABLE IF EXISTS release_waivers;
DROP TABLE IF EXISTS release_gate_results;
DROP TABLE IF EXISTS audit_partitions;
DROP TABLE IF EXISTS codebook_releases;
