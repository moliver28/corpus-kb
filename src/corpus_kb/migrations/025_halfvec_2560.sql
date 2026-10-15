-- ============================================================================
-- Migration 025: halfvec(2560) dimension regime (config-alignment W1)
-- ============================================================================
-- The default embedder flips to qwen3-embedding:4b at its NATIVE 2560 dims
-- and the main vector column becomes halfvec(2560). halfvec is REQUIRED at
-- this width: pgvector HNSW caps the plain `vector` type at 2000 dims and
-- `halfvec` at 4000, so the 2560-dim column finally gets a REAL hnsw index
-- (halfvec_cosine_ops, m = 16, ef_construction = 128 — mirrored by
-- retrieval.hnsw in config; doctor cross-checks the live index options).
--
-- The model switch invalidates every stored embedding (old vectors are
-- 4096-dim from another model; converting that data is meaningless). This
-- migration RETYPES, it does not convert: existing vectors become NULL
-- (stale) and rebuild via `corpus-kb ingest-reembed`, which now targets
-- stale rows too. Text, documents and chunks are untouched.
--
-- NOT touched (separate contracts): the research grain (016/021 —
-- research_units/research_exchanges/shadow vectors stay vector(1024) with
-- halfvec cast-expression indexes) and every shadow table.
--
-- Idempotency: every step is guarded on the catalog. The upgrade path runs
-- this on the old vector(4096) shape; a fresh install (storage/schema.sql
-- already has the halfvec end state, empty tables) no-ops every guard. The
-- stale-row retype is a one-way data decision by design — rollback_025
-- restores the 4096 column SHAPE but cannot restore the old vectors.
-- ============================================================================

SET app.current_tenant_id = '00000000-0000-0000-0000-000000000001';

-- ----------------------------------------------------------------------------
-- 1. chunks_vectors: drop the indexes bound to the old column shape. The
-- ivfflat (lists = 100) and the m16/ef200 hnsw attempts were both dead on
-- the 4096-dim `vector` column (pgvector caps ivfflat/hnsw at 2000 dims);
-- they are replaced by one working halfvec index below.
-- ----------------------------------------------------------------------------

DROP INDEX IF EXISTS idx_chunks_vectors_ivfflat;
DROP INDEX IF EXISTS idx_chunks_vectors_hnsw;

-- ----------------------------------------------------------------------------
-- 2. Rename `vector` -> `embedding` (skipped when already renamed, i.e. the
-- fresh-install schema.sql end state).
-- ----------------------------------------------------------------------------

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'chunks_vectors'
          AND column_name = 'vector'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'chunks_vectors'
          AND column_name = 'embedding'
    ) THEN
        ALTER TABLE chunks_vectors RENAME COLUMN vector TO embedding;
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- 3. Retype to halfvec(2560). USING NULL: the old vector(4096) data belongs
-- to another model's space and cannot convert — rows become stale and
-- rebuild via ingest-reembed. The NOT NULL check constraint is dropped
-- FIRST (it would fail the ALTER on any populated table, and stale NULL
-- rows are a supported state in this regime). Fresh halfvec installs skip
-- the retype (the type already matches).
-- ----------------------------------------------------------------------------

ALTER TABLE chunks_vectors DROP CONSTRAINT IF EXISTS vector_not_null;
ALTER TABLE chunks_vectors DROP CONSTRAINT IF EXISTS embedding_not_null;

DO $$
DECLARE
    col_type TEXT;
BEGIN
    SELECT format_type(a.atttypid, a.atttypmod) INTO col_type
    FROM pg_attribute a
    WHERE a.attrelid = 'public.chunks_vectors'::regclass
      AND a.attname = 'embedding';
    IF col_type = 'vector(4096)' THEN
        ALTER TABLE chunks_vectors
            ALTER COLUMN embedding TYPE halfvec(2560) USING NULL;
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- 4. The embedding_model default tracks the shipped model (fresh rows
-- inserted without an explicit model label report the current contract).
-- ----------------------------------------------------------------------------

ALTER TABLE chunks_vectors
    ALTER COLUMN embedding_model SET DEFAULT 'qwen3-embedding:4b';

-- ----------------------------------------------------------------------------
-- 5. Matryoshka slice recompute. Stale rows lose their old-model slice;
-- live rows get the slice cut from the new column. The write path keeps
-- maintaining vector_1024 per row after this.
-- ----------------------------------------------------------------------------

UPDATE chunks_vectors SET vector_1024 = NULL
WHERE embedding IS NULL AND vector_1024 IS NOT NULL;

UPDATE chunks_vectors
SET vector_1024 = l2_normalize(subvector(embedding::vector, 1, 1024))
WHERE embedding IS NOT NULL AND vector_1024 IS NULL;

-- ----------------------------------------------------------------------------
-- 6. The real HNSW index on the halfvec column. Requires pgvector >= 0.7
-- (halfvec opclass); older servers skip the index with a warning and search
-- falls back to exact scans (same guarded pattern as migrations 010/021).
-- ----------------------------------------------------------------------------

DO $$
BEGIN
    EXECUTE 'CREATE INDEX IF NOT EXISTS idx_chunks_vectors_hnsw_halfvec2560 '
            'ON chunks_vectors USING hnsw (embedding halfvec_cosine_ops) '
            'WITH (m = 16, ef_construction = 128)';
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Skipping idx_chunks_vectors_hnsw_halfvec2560: %', SQLERRM;
END $$;

-- ----------------------------------------------------------------------------
-- 7. Migration 012 coding vector columns -> halfvec(2560) (same retype:
-- stale NULLs, rebuild by the coding pipeline's own vector jobs). No
-- indexes reference these columns; only answer_ctx_vectors.vector carries
-- a NOT NULL, dropped first for the same stale-row reason as above.
-- ----------------------------------------------------------------------------

DO $$
DECLARE
    tables TEXT[] := ARRAY['code_registry', 'code_registry', 'answer_ctx_vectors', 'chunk_codes'];
    cols TEXT[] := ARRAY['definition_vector', 'probe_vector', 'vector', 'rationale_vector'];
    i INT;
    col_type TEXT;
BEGIN
    FOR i IN 1..4 LOOP
        SELECT format_type(a.atttypid, a.atttypmod) INTO col_type
        FROM pg_attribute a
        WHERE a.attrelid = format('public.%I', tables[i])::regclass
          AND a.attname = cols[i];
        IF col_type = 'vector(4096)' THEN
            EXECUTE format(
                'ALTER TABLE public.%I ALTER COLUMN %I DROP NOT NULL',
                tables[i], cols[i]);
            EXECUTE format(
                'ALTER TABLE public.%I ALTER COLUMN %I TYPE halfvec(2560) USING NULL',
                tables[i], cols[i]);
        END IF;
    END LOOP;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Coding vector retype skipped: %', SQLERRM;
END $$;
