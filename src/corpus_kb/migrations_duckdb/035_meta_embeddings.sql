-- DuckDB twin of pg migration 034 (meta_embeddings, Living Corpus W6).
-- Translation per the house convention (see 034 header): UUID -> VARCHAR,
-- halfvec(2560) -> FLOAT[2560]. DuckDB has no RLS (single-writer file
-- engine); tenant isolation is application-level there.

CREATE TABLE IF NOT EXISTS meta_embeddings (
    meta_id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR NOT NULL,
    meta_kind TEXT NOT NULL,
    meta_key TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    -- FLOAT[__EMBEDDING_DIMS__] is substituted by the migration runner from
    -- embedding.dimensions (issue #559); see _setup/store_width.py.
    embedding FLOAT[__EMBEDDING_DIMS__] NOT NULL,
    source_count INT NOT NULL DEFAULT 0,
    built_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, meta_kind, meta_key)
);

CREATE INDEX IF NOT EXISTS idx_meta_embeddings_kind
    ON meta_embeddings(tenant_id, meta_kind);
