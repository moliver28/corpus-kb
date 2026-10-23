-- DuckDB twin of pg migration 030 (extraction cache, Living Corpus W1).
-- Type conversions per the house convention (see 029 header):
--   jsonb -> JSON, TEXT stays TEXT. No RLS: the DuckDB gateway enforces
--   the tenant predicate per statement, and the extraction stage never
--   touches this table on DuckDB runs (pool is None), so it exists to
--   keep the storage-portability parity and digest legs complete.

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
