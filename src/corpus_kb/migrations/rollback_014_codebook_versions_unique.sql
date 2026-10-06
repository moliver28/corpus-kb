-- ============================================================================
-- Migration 014 Rollback: Drop the codebook content-hash uniqueness constraint
-- ============================================================================
-- Reverts only the constraint. The duplicate codebook_versions rows migration
-- 014 collapsed are not resurrected: they were redundant copies of a codebook
-- with an identical sha256, and nothing references them.
--
-- Rolling this back re-opens the duplicate-version bug for any loader still
-- naming (tenant_id, sha256) as its ON CONFLICT target, which will then fail
-- with "no unique or exclusion constraint matching the ON CONFLICT
-- specification". Roll back src/corpus_kb/coding/codebook_loader.py with it.

ALTER TABLE codebook_versions
    DROP CONSTRAINT IF EXISTS codebook_versions_tenant_sha256_key;
