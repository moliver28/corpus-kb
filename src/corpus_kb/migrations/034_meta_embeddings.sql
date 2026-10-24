-- Coarse-grained corpus aggregates (Living Corpus W6). One row per
-- high-level corpus object — topic, belief_summary, document, profile —
-- as a single halfvec(2560) (the migration 025 regime). Consumers: the
-- search pipeline's meta arm (coarse routing hits) and analysis ops.
-- Rebuild is idempotent per (tenant, meta_kind, meta_key).

CREATE TABLE IF NOT EXISTS meta_embeddings (
    meta_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    meta_kind TEXT NOT NULL
        CHECK (meta_kind IN ('topic', 'belief_summary', 'document', 'profile')),
    meta_key TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    embedding halfvec(2560) NOT NULL,
    source_count INT NOT NULL DEFAULT 0 CHECK (source_count >= 0),
    built_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, meta_kind, meta_key)
);

-- The meta arm is a small top-k cosine scan (one row per topic /
-- document / belief, hundreds at most), so a plain index scan is the
-- right plan; no HNSW here — the chunk-level index (migration 025)
-- stays the only one.
CREATE INDEX IF NOT EXISTS idx_meta_embeddings_kind
    ON meta_embeddings(tenant_id, meta_kind);

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['meta_embeddings'] LOOP
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
