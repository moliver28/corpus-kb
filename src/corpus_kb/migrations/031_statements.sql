-- Statement atoms (Living Corpus W2): the atomic, bi-temporal units the
-- corpus reasons over. One row per (tenant, statement_hash, chunk): the
-- same sentence re-ingested in an unchanged chunk reuses its row. The
-- supersede columns are reserved for the W3 conflict cascade — atoms are
-- never deleted, only superseded.

CREATE TABLE IF NOT EXISTS statements (
    statement_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    chunk_id UUID NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    statement_hash CHAR(64) NOT NULL,
    text TEXT NOT NULL,
    template_id TEXT NOT NULL,
    template_version TEXT NOT NULL,
    start_char INT,
    end_char INT,
    entities JSONB NOT NULL,
    exchange_id BIGINT,
    valid_at TIMESTAMPTZ,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    confidence FLOAT NOT NULL DEFAULT 0.5,
    superseded_by UUID,
    metadata JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, statement_hash, chunk_id)
);

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['statements'] LOOP
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

CREATE INDEX IF NOT EXISTS idx_statements_tenant_chunk
    ON statements(tenant_id, chunk_id);
CREATE INDEX IF NOT EXISTS idx_statements_tenant_valid
    ON statements(tenant_id, valid_at NULLS LAST, observed_at, statement_id);
CREATE INDEX IF NOT EXISTS idx_statements_tenant_template
    ON statements(tenant_id, template_id);
