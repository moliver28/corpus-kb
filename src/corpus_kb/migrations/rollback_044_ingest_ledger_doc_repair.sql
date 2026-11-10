-- ============================================================================
-- Rollback migration 044: the ledger re-point stays.
-- ============================================================================
-- The forward step repairs DATA (it re-points ledger rows at the document
-- rows they already describe); there is no schema shape to reverse, and
-- re-scattering the ids would re-create the corruption the repair fixes.
-- ============================================================================
SELECT 1;
