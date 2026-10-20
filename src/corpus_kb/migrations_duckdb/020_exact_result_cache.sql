-- ============================================================================
-- Migration 020: provenance-keyed exact result cache (DuckDB mirror)
-- ============================================================================
-- Pair of migrations/020_exact_result_cache.sql. One immutable cache table.
-- The two immutability triggers are dropped (bodies as comments below); the
-- gateway blocks UPDATE and DELETE on this table — invalidation is by key
-- change, and cleanup is an explicit operator TRUNCATE.
-- ============================================================================

CREATE TABLE IF NOT EXISTS research_exact_cache (
    cache_key CHAR(64) PRIMARY KEY,
    tenant_id VARCHAR NOT NULL,
    -- The full provenance tuple, component by component (hex sha256 each or
    -- the canonical JSON of the decoding params).
    component_hashes JSON NOT NULL,
    -- The cached LLM result (verbatim JSON payload of the model reply).
    response JSON NOT NULL,
    model_name TEXT NOT NULL,
    model_digest TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_exact_cache_tenant
    ON research_exact_cache(tenant_id, created_at);

-- Dropped triggers (gateway enforcement, program doc 8):
-- trg_exact_cache_no_update (BEFORE UPDATE) and trg_exact_cache_no_delete
-- (BEFORE DELETE), both running corpus.forbid_exact_cache_mutation():
-- raise 'research_exact_cache is immutable: <op> is not allowed
-- (invalidation is by key change; cleanup is operator TRUNCATE)'.
