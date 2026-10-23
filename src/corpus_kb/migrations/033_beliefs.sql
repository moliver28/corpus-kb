-- Deep Bayesian beliefs (Living Corpus W4). A belief is the current
-- Beta posterior over one subject-relation-object stance; the revision
-- log is append-only evidence (statement id + discounted weight), so
-- every posterior is exactly recomputable and the full history survives.

CREATE TABLE IF NOT EXISTS beliefs (
    belief_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    subject TEXT NOT NULL,
    relation TEXT NOT NULL,
    object TEXT NOT NULL,
    alpha DOUBLE PRECISION NOT NULL,
    beta DOUBLE PRECISION NOT NULL,
    evidence_count INT NOT NULL DEFAULT 0,
    era_count INT NOT NULL DEFAULT 1,
    prior_strength DOUBLE PRECISION NOT NULL DEFAULT 2.0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, subject, relation, object)
);

CREATE TABLE IF NOT EXISTS belief_revisions (
    revision_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id UUID NOT NULL,
    belief_id UUID NOT NULL REFERENCES beliefs(belief_id) ON DELETE CASCADE,
    statement_id UUID,
    stance TEXT NOT NULL CHECK (stance IN ('positive','negative')),
    weight DOUBLE PRECISION NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['beliefs', 'belief_revisions'] LOOP
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

CREATE INDEX IF NOT EXISTS idx_beliefs_tenant_subject
    ON beliefs(tenant_id, subject, relation, object);
CREATE INDEX IF NOT EXISTS idx_belief_revisions_belief
    ON belief_revisions(tenant_id, belief_id, observed_at);
