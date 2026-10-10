-- ============================================================================
-- Migration 020: provenance-keyed exact result cache (U42)
-- ============================================================================
-- Immutable cache rows for LLM coding/judgment results. The key is the
-- SHA-256 of the full provenance tuple (unit text hash, codebook release
-- hash, prompt-template hash, model digest, decoding params, schema hash) —
-- EXACT match only, deliberately NO semantic caching (a near-hit on a coding
-- decision is a false hit). Any component change derives a new key, so
-- entries are invalidated naturally and never mutated: UPDATE/DELETE raise
-- through the immutability trigger.
--
-- RLS mirrors the research tables (migration 016 pattern): the tenant GUC
-- policy with FORCE ROW LEVEL SECURITY, so a cached read can never cross
-- tenants even if a key collided (SHA-256 makes that astronomically
-- unlikely, but the tenant WHERE clause is the second lock).
--
-- Idempotent: CREATE TABLE IF NOT EXISTS + DROP POLICY IF EXISTS before
-- CREATE POLICY, matching the house migration convention.
-- ============================================================================

SET app.current_tenant_id = '00000000-0000-0000-0000-000000000001';

-- ----------------------------------------------------------------------------
-- 1. research_exact_cache — immutable exact-match result rows
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS research_exact_cache (
    cache_key CHAR(64) PRIMARY KEY,
    tenant_id UUID NOT NULL,
    -- The full provenance tuple, component by component (hex sha256 each or
    -- the canonical JSON of the decoding params) — auditable and sufficient
    -- to recompute cache_key.
    component_hashes JSONB NOT NULL,
    -- The cached LLM result (verbatim JSON payload of the model reply).
    response JSONB NOT NULL,
    model_name TEXT NOT NULL,
    model_digest TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_exact_cache_tenant
    ON research_exact_cache(tenant_id, created_at);

-- ----------------------------------------------------------------------------
-- 2. RLS: tenant isolation (migration 016 policy pattern)
-- ----------------------------------------------------------------------------

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['research_exact_cache'] LOOP
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

-- ----------------------------------------------------------------------------
-- 3. Immutability: cache rows are write-once. Updates and deletes raise —
-- invalidation happens by KEY CHANGE (new provenance derives a new key),
-- never by mutation. Cleanup is an explicit operator TRUNCATE, not an
-- application DELETE.
-- ----------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION corpus.forbid_exact_cache_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'research_exact_cache is immutable: % is not allowed '
                    '(invalidation is by key change; cleanup is operator TRUNCATE)',
                    TG_OP;
END;
$$;

DROP TRIGGER IF EXISTS trg_exact_cache_no_update ON research_exact_cache;
CREATE TRIGGER trg_exact_cache_no_update
    BEFORE UPDATE ON research_exact_cache
    FOR EACH ROW EXECUTE FUNCTION corpus.forbid_exact_cache_mutation();

DROP TRIGGER IF EXISTS trg_exact_cache_no_delete ON research_exact_cache;
CREATE TRIGGER trg_exact_cache_no_delete
    BEFORE DELETE ON research_exact_cache
    FOR EACH ROW EXECUTE FUNCTION corpus.forbid_exact_cache_mutation();
