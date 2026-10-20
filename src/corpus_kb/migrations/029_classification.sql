-- ============================================================================
-- Migration 029: few-shot classification layer (WP-3, analysis program)
-- ============================================================================
-- Two tenant-scoped tables for the `classification_build` /
-- `classification_list` ops:
--
--   * `label_sets` — the seed label set: the class list (`classes`
--     jsonb) the classifier is fit against. Seed sets are INPUT, not
--     derived: they survive a rebuild of the predictions.
--   * `chunk_predictions` — the per-chunk prediction: one row per chunk
--     per label set (UNIQUE), the raw and calibrated probabilities, the
--     model tier (head / setfit), the review flag (low confidence routes
--     to the review queue, the coding review-surface pattern) and the
--     recorded model parameters (`params` jsonb, the determinism
--     contract: seeds, versions, tier). Rows are a DERIVED, rebuildable
--     artifact: classification_build deletes the tenant's rows for the
--     set wholesale before inserting the new build.
--
-- RLS mirrors 026: ENABLE + FORCE + tenant GUC policy per table, so a
-- prediction read can never cross tenants.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS + DROP POLICY IF EXISTS before
-- CREATE POLICY, matching the house migration convention.
-- ============================================================================

SET app.current_tenant_id = '00000000-0000-0000-0000-000000000001';

-- ----------------------------------------------------------------------------
-- 1. label_sets — the seed label sets (input, kept)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS label_sets (
    set_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    name TEXT NOT NULL,
    classes JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ----------------------------------------------------------------------------
-- 2. chunk_predictions — the per-chunk prediction (derived, replaceable)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS chunk_predictions (
    tenant_id UUID NOT NULL,
    chunk_id UUID NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    set_id UUID NOT NULL REFERENCES label_sets(set_id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    probability DOUBLE PRECISION NOT NULL DEFAULT 0.0
        CHECK (probability >= 0.0 AND probability <= 1.0),
    calibrated_probability DOUBLE PRECISION
        CHECK (calibrated_probability IS NULL
               OR (calibrated_probability >= 0.0 AND calibrated_probability <= 1.0)),
    tier TEXT NOT NULL,
    review BOOLEAN NOT NULL DEFAULT FALSE,
    params JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, chunk_id, set_id)
);

CREATE INDEX IF NOT EXISTS idx_label_sets_tenant
    ON label_sets(tenant_id);
CREATE INDEX IF NOT EXISTS idx_chunk_predictions_set
    ON chunk_predictions(tenant_id, set_id);

-- ----------------------------------------------------------------------------
-- 3. RLS: tenant isolation (migration 026 policy pattern, ENABLE + FORCE)
-- ----------------------------------------------------------------------------

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['label_sets', 'chunk_predictions'] LOOP
        BEGIN
            EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
            EXECUTE format('DROP POLICY IF EXISTS %I_tenant_isolation ON %I', t, t);
            EXECUTE format(
                'CREATE POLICY %I_tenant_isolation ON %I '
                'USING (tenant_id = NULLIF(current_setting(''app.current_tenant_id'', true), '''')::UUID) '
                'WITH CHECK (tenant_id = NULLIF(current_setting(''app.current_tenant_id'', true), '''')::UUID)',
                t, t);
            EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING 'RLS setup failed for %: %', t, SQLERRM;
        END;
    END LOOP;
END $$;
