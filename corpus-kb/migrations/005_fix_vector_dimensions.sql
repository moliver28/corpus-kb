-- ============================================================================
-- Migration 005: Fix vector dimensions from 4096 to 768
-- ============================================================================
-- The chunks_vectors table in older versions was created with vector(4096),
-- but the configured embedding model (nomic-embed-text) produces 768-dimensional
-- vectors. This migration drops and recreates the table with the correct dimensions.
--
-- Note: This is safe because chunks_vectors contains DERIVED DATA (embeddings
-- computed from chunks). Dropping it will not lose source data, and re-ingestion
-- will recreate the vectors.

-- Drop old chunks_vectors table if it exists (with old dimensions)
DROP TABLE IF EXISTS chunks_vectors CASCADE;

-- Recreate chunks_vectors with correct dimensions (768)
CREATE TABLE IF NOT EXISTS chunks_vectors (
    chunk_id UUID PRIMARY KEY REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL,
    vector vector(768),
    embedding_model VARCHAR(255) NOT NULL DEFAULT 'nomic-embed-text',
    embedded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT vector_not_null CHECK (vector IS NOT NULL)
);

-- Create indexes (with exception handlers for databases that don't support them)
DO $$
BEGIN
    EXECUTE 'CREATE INDEX idx_chunks_vectors_ivfflat ON chunks_vectors USING ivfflat (vector vector_cosine_ops) WITH (lists = 100)';
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Skipping ivfflat index on chunks_vectors.vector: %', SQLERRM;
END $$;

DO $$
BEGIN
    EXECUTE 'CREATE INDEX IF NOT EXISTS idx_chunks_vectors_hnsw ON chunks_vectors USING hnsw (vector vector_cosine_ops) WITH (m = 16, ef_construction = 200)';
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Skipping hnsw index on chunks_vectors.vector: %', SQLERRM;
END $$;

CREATE INDEX idx_chunks_vectors_tenant ON chunks_vectors(tenant_id);
CREATE INDEX idx_chunks_vectors_model ON chunks_vectors(embedding_model);

-- Enable RLS
ALTER TABLE chunks_vectors ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    CREATE POLICY chunks_vectors_tenant_isolation ON chunks_vectors
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID);
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Policy chunks_vectors_tenant_isolation already exists: %', SQLERRM;
END $$;
