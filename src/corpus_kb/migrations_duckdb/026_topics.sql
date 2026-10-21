-- ============================================================================
-- Migration 026: topic layer (DuckDB mirror, 2 tables)
-- ============================================================================
-- Pair of migrations/026_topics.sql. Translation: UUID -> VARCHAR,
-- jsonb -> JSON, DOUBLE PRECISION -> DOUBLE. FK ON DELETE CASCADE actions
-- are Postgres constraint clauses; the gateway enforces the delete
-- behavior. The RLS block is dropped. idx_topics_tenant_size keeps its
-- columns without the Postgres DESC hint (DuckDB ignores index ordering).
-- ============================================================================

-- 1. topics: the discovered topic rows
CREATE TABLE IF NOT EXISTS topics (
    topic_id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR NOT NULL,
    label TEXT NOT NULL,
    reduced_label TEXT,
    size INT NOT NULL CHECK (size >= 0),
    keywords JSON NOT NULL DEFAULT '[]',
    params JSON NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2. chunk_topics: the per-chunk assignment (one topic per chunk)
CREATE TABLE IF NOT EXISTS chunk_topics (
    tenant_id VARCHAR NOT NULL,
    chunk_id VARCHAR NOT NULL REFERENCES chunks(chunk_id),
    -- Postgres action: ON DELETE CASCADE.
    topic_id VARCHAR NOT NULL REFERENCES topics(topic_id),
    -- Postgres action: ON DELETE CASCADE.
    probability DOUBLE NOT NULL DEFAULT 0.0
        CHECK (probability >= 0.0 AND probability <= 1.0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_chunk_topics_topic
    ON chunk_topics(tenant_id, topic_id);
CREATE INDEX IF NOT EXISTS idx_topics_tenant_size
    ON topics(tenant_id, size);
