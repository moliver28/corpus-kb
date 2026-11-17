-- DuckDB twin of pg migration 030 (extraction cache, Living Corpus W1).
-- Type conversions per the house convention (see 029 header):
--   jsonb -> JSON, TEXT stays TEXT. No RLS: the cache statements carry
--   the tenant predicate explicitly (extraction/cache.py), and cache
--   reads and writes run through the gateway statement gate on DuckDB
--   runs — same statements, same key semantics as the Postgres twin.

CREATE TABLE IF NOT EXISTS extraction_cache (
    tenant_id UUID NOT NULL,
    chunk_hash VARCHAR(64) NOT NULL,
    backend TEXT NOT NULL,
    model_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    entities JSON NOT NULL,
    relations JSON NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, chunk_hash, backend, model_version, prompt_version)
);

CREATE INDEX IF NOT EXISTS idx_extraction_cache_tenant_backend
    ON extraction_cache(tenant_id, backend, model_version, prompt_version);
