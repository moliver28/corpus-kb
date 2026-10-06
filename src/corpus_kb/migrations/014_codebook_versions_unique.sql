-- ============================================================================
-- Migration 014: UNIQUE (tenant_id, sha256) on codebook_versions
-- ============================================================================
-- Migration 012 gave codebook_versions only plain indexes on tenant_id and
-- sha256. src/corpus_kb/coding/codebook_loader.py's load_codebook dedupes by
-- content hash with
--   INSERT ... ON CONFLICT (tenant_id, sha256) DO NOTHING RETURNING version_id
-- and falls back to a SELECT by sha256 when the insert returns nothing. With
-- no unique constraint for ON CONFLICT to target, the insert (fresh
-- gen_random_uuid() PK) ALWAYS succeeds: the fallback was dead code, and every
-- re-submit of an identical codebook minted a new version plus a duplicate set
-- of code_registry rows whose operator-calibrated pool_floor / residual_floor
-- came back NULL. A re-load silently regressed a tuned floor to the default.
--
-- This migration makes the dedupe real. load_codebook now names the conflict
-- target explicitly, so it needs this constraint to exist.
--
-- Pre-existing duplicates (minted by the pre-fix loader) are collapsed first,
-- keeping the OLDEST version per (tenant_id, sha256) and carrying any
-- calibrated floors from the doomed rows onto the survivors before dropping
-- them. Nothing else in the schema references codebook_version_id, so removing
-- the redundant code_registry rows leaves no dangling reference.
--
-- FORCE row-level security (migration 013) applies to the table OWNER, which
-- is the role that runs this migration, so the cleanup below would otherwise
-- only see whichever single tenant app.current_tenant_id happens to name.
-- FORCE is lifted for the cleanup and restored in the same transaction,
-- before the constraint is added.
-- ============================================================================

ALTER TABLE codebook_versions NO FORCE ROW LEVEL SECURITY;
ALTER TABLE code_registry NO FORCE ROW LEVEL SECURITY;

CREATE TEMP TABLE codebook_version_dupes ON COMMIT DROP AS
WITH keepers AS (
    SELECT DISTINCT ON (tenant_id, sha256) tenant_id, sha256, version_id
    FROM codebook_versions
    ORDER BY tenant_id, sha256, created_at, version_id
)
SELECT v.version_id AS dupe_id, k.version_id AS keeper_id
FROM codebook_versions v
JOIN keepers k USING (tenant_id, sha256)
WHERE v.version_id <> k.version_id;

-- Calibration survives the collapse: a floor tuned on a duplicate row moves to
-- the surviving row when the survivor has none of its own.
UPDATE code_registry keep
SET pool_floor = COALESCE(keep.pool_floor, src.pool_floor),
    residual_floor = COALESCE(keep.residual_floor, src.residual_floor)
FROM code_registry src
JOIN codebook_version_dupes d ON d.dupe_id = src.codebook_version_id
WHERE keep.codebook_version_id = d.keeper_id
  AND keep.tenant_id = src.tenant_id
  AND keep.code_id = src.code_id
  AND (keep.pool_floor IS NULL OR keep.residual_floor IS NULL)
  AND (src.pool_floor IS NOT NULL OR src.residual_floor IS NOT NULL);

DELETE FROM code_registry
WHERE codebook_version_id IN (SELECT dupe_id FROM codebook_version_dupes);

DELETE FROM codebook_versions
WHERE version_id IN (SELECT dupe_id FROM codebook_version_dupes);

ALTER TABLE codebook_versions FORCE ROW LEVEL SECURITY;
ALTER TABLE code_registry FORCE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'codebook_versions_tenant_sha256_key'
          AND conrelid = 'codebook_versions'::regclass
    ) THEN
        ALTER TABLE codebook_versions
            ADD CONSTRAINT codebook_versions_tenant_sha256_key
            UNIQUE (tenant_id, sha256);
    END IF;
END $$;

-- The constraint's implicit index covers the sha256 lookups
-- idx_codebook_versions_sha256 served, but that index also answers
-- cross-tenant sha256 probes, so it stays.
