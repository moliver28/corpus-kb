-- ============================================================================
-- Migration 028: weak-supervision labeling layer (WP-2, analysis program)
-- ============================================================================
-- Two tenant-scoped tables for the `labeling_build` / `labeling_list` /
-- `labeling_lfs` ops:
--
--   * `label_functions` — the user-authored labeling functions: the LF
--     kind (regex, lexicon, negation-guarded regex, metadata predicate)
--     and its full config record (`config` jsonb). LFs are INPUT, not
--     derived: they survive a rebuild of the labels.
--   * `chunk_labels` — the aggregated per-chunk label: exactly one label
--     per chunk per tenant (UNIQUE), the Dawid-Skene posterior
--     (`probability`) and the per-LF detail (`detail` jsonb). Rows are a
--     DERIVED, rebuildable artifact: labeling_build deletes the tenant's
--     rows wholesale before inserting the new build.
--
-- RLS mirrors 026: ENABLE + FORCE + tenant GUC policy per table, so a
-- label read can never cross tenants.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS + DROP POLICY IF EXISTS before
-- CREATE POLICY, matching the house migration convention.
-- ============================================================================

SET app.current_tenant_id = '00000000-0000-0000-0000-000000000001';

-- ----------------------------------------------------------------------------
-- 1. label_functions — the authored labeling functions (input, kept)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS label_functions (
    lf_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    config JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ----------------------------------------------------------------------------
-- 2. chunk_labels — the aggregated per-chunk label (derived, replaceable)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS chunk_labels (
    tenant_id UUID NOT NULL,
    chunk_id UUID NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    probability DOUBLE PRECISION NOT NULL DEFAULT 0.0
        CHECK (probability >= 0.0 AND probability <= 1.0),
    detail JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_label_functions_tenant
    ON label_functions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_chunk_labels_tenant_label
    ON chunk_labels(tenant_id, label);

-- ----------------------------------------------------------------------------
-- 3. RLS: tenant isolation (migration 026 policy pattern, ENABLE + FORCE)
-- ----------------------------------------------------------------------------

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['label_functions', 'chunk_labels'] LOOP
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
