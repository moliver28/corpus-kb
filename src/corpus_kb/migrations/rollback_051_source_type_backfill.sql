-- ============================================================================
-- Rollback migration 051: the canonical backfill stays.
-- ============================================================================
-- The forward step repairs DATA (legacy alias web rows re-stamp to the W3
-- canonical external-site). After the backfill, a backfilled row is
-- indistinguishable from a row W3 stamped canonically at ingest, so a
-- reverse UPDATE would corrupt canonical rows. There is no schema shape to
-- reverse, and re-scattering the names would re-create the drift the
-- backfill fixes. This follows the migration 044 rollback precedent.
-- ============================================================================

SELECT 1;
