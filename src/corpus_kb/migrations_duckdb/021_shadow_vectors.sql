-- ============================================================================
-- Migration 021: side-by-side shadow hierarchy index (DuckDB mirror)
-- ============================================================================
-- Pair of migrations/021_shadow_vectors.sql. Two tables for the multi-
-- granularity shadow index. Translation: UUID -> VARCHAR,
-- vector(1024) -> FLOAT[1024], jsonb -> JSON.
--   * shadow_chunks.parent_id is a self-referencing FK. DuckDB FKs cannot
--     carry ON DELETE CASCADE; the gateway enforces the delete action.
--   * The HNSW index on the halfvec cast expression is dropped; DuckDB
--     defaults to exact scan.
-- ============================================================================

-- 1. shadow_chunks: the three-level text hierarchy
CREATE TABLE IF NOT EXISTS shadow_chunks (
    shadow_chunk_id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR NOT NULL,
    doc_id VARCHAR NOT NULL REFERENCES documents(doc_id),
    -- Postgres action: ON DELETE CASCADE.
    level TEXT NOT NULL CHECK (level IN ('child','chunk','parent')),
    seq INT NOT NULL,
    parent_id VARCHAR REFERENCES shadow_chunks(shadow_chunk_id),
    -- Postgres action: ON DELETE CASCADE.
    text TEXT NOT NULL,
    token_count INT NOT NULL,
    start_char INT,
    end_char INT,
    boundary TEXT,
    -- content hash of the SOURCE DOCUMENT (rebuild trigger), not of this row
    source_hash CHAR(64),
    path TEXT NOT NULL DEFAULT 'main',
    metadata JSON NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, doc_id, path, level, seq)
);

CREATE INDEX IF NOT EXISTS idx_shadow_chunks_parent
    ON shadow_chunks(tenant_id, parent_id);
CREATE INDEX IF NOT EXISTS idx_shadow_chunks_doc_level
    ON shadow_chunks(tenant_id, doc_id, path, level);

-- 2. shadow_vectors: embeddings for the retrieval levels (child + chunk)
CREATE TABLE IF NOT EXISTS shadow_vectors (
    shadow_chunk_id VARCHAR PRIMARY KEY REFERENCES shadow_chunks(shadow_chunk_id),
    -- Postgres action: ON DELETE CASCADE.
    tenant_id VARCHAR NOT NULL,
    embedding FLOAT[1024],
    embedding_model TEXT NOT NULL DEFAULT 'nomic-embed-text',
    dimensions INT,
    embedded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT shadow_vector_not_null CHECK (embedding IS NOT NULL)
);
