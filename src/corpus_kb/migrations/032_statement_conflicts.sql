-- Statement conflicts (Living Corpus W3): rows are FLAGS, never deletes.
-- Gate A temporal-overlap separates a revision from a live conflict;
-- Gate B records the structured triple-swap evidence; Gate C records the
-- NLI verdict. A human resolve (research_reviews flow) finalizes.

CREATE TABLE IF NOT EXISTS statement_conflicts (
    conflict_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    statement_a_id UUID NOT NULL REFERENCES statements(statement_id) ON DELETE CASCADE,
    statement_b_id UUID NOT NULL REFERENCES statements(statement_id) ON DELETE CASCADE,
    anchor_name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('relation_swap','entity_swap','temporal')),
    gate_reached TEXT NOT NULL CHECK (gate_reached IN ('A','B','C')),
    verdict TEXT NOT NULL CHECK (verdict IN ('conflict','revision','uncertain')),
    nli_verdict TEXT,
    resolved_verdict TEXT CHECK (resolved_verdict IN ('conflict','revision','dismissed')),
    resolved_by TEXT,
    metadata JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, statement_a_id, statement_b_id, kind)
);

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['statement_conflicts'] LOOP
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

CREATE INDEX IF NOT EXISTS idx_statement_conflicts_tenant_verdict
    ON statement_conflicts(tenant_id, resolved_verdict NULLS FIRST, verdict, created_at);
