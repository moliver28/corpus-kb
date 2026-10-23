-- Extraction cache: one row per (chunk, backend, model, prompt) extraction
-- result. Makes re-ingest of unchanged content free — the extraction model
-- pass is the most expensive ingest stage. Empty entity/relation payloads
-- are valid rows (the model genuinely found nothing).

CREATE TABLE IF NOT EXISTS extraction_cache (
    tenant_id UUID NOT NULL,
    chunk_hash VARCHAR(64) NOT NULL,
    backend TEXT NOT NULL,
    model_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    entities JSONB NOT NULL DEFAULT '[]'::jsonb,
    relations JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, chunk_hash, backend, model_version, prompt_version)
);

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['extraction_cache'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS %I ON %I', t || '_tenant_isolation', t);
        EXECUTE format(
            'CREATE POLICY %I ON %I USING (tenant_id = NULLIF(current_setting(''app.current_tenant_id'', true), '''')::UUID) '
            'WITH CHECK (tenant_id = NULLIF(current_setting(''app.current_tenant_id'', true), '''')::UUID)',
            t || '_tenant_isolation', t
        );
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    END LOOP;
END $$;

CREATE INDEX IF NOT EXISTS idx_extraction_cache_tenant_backend
    ON extraction_cache(tenant_id, backend, model_version, prompt_version);
