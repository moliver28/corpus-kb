-- ============================================================================
-- Migration 010: matryoshka two-tier ANN index
-- ============================================================================
-- Adds a front-sliced, L2-renormalized vector(1024) alongside the existing
-- exact vector(4096). pgvector indexes only <= 2000 dims (confirmed by the
-- guarded, currently-failing ivfflat/hnsw attempts on the 4096d column in
-- migration 004), so vector_1024 gets a real HNSW index for fast candidate
-- generation; the query layer rescores candidates on the exact 4096d
-- column. Verified locally: pgvector 0.8.2 has subvector()/l2_normalize()
-- and the in-SQL backfill below works directly -- no Python fallback needed.

SET app.current_tenant_id = '00000000-0000-0000-0000-000000000001';

ALTER TABLE chunks_vectors ADD COLUMN IF NOT EXISTS vector_1024 vector(1024);

UPDATE chunks_vectors
SET vector_1024 = l2_normalize(subvector(vector, 1, 1024))
WHERE vector_1024 IS NULL AND vector IS NOT NULL;

DO $$
BEGIN
  EXECUTE 'CREATE INDEX IF NOT EXISTS idx_chunks_vectors_hnsw_1024 ON chunks_vectors '
          'USING hnsw (vector_1024 vector_cosine_ops) WITH (m = 16, ef_construction = 200)';
EXCEPTION WHEN OTHERS THEN
  RAISE WARNING 'Skipping idx_chunks_vectors_hnsw_1024: %', SQLERRM;
END $$;
