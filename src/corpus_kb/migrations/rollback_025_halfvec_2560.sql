-- ============================================================================
-- Rollback migration 025: restore the vector(4096) column shape.
-- ============================================================================
-- Restores the pre-025 column NAMES, TYPES and DEFAULTS — NOT the data:
-- the old 4096-dim vectors were dropped by the retype (USING NULL) and no
-- other model can regenerate them. Rows stay NULL (stale) after rollback;
-- the hnsw halfvec index is dropped because pgvector caps hnsw on `vector`
-- at 2000 dims (the pre-025 "indexes" on the 4096-dim column never worked
-- and are not recreated).
--
-- The `vector_not_null` check constraint is deliberately NOT restored: the
-- stale-NULL row state it would forbid is exactly what this table holds
-- after either direction of the retype. Re-apply 025 afterwards to return
-- to the halfvec(2560) regime.
-- ============================================================================

SET app.current_tenant_id = '00000000-0000-0000-0000-000000000001';

-- 1. Drop the halfvec index (bound to the column we are about to retype).
DROP INDEX IF EXISTS idx_chunks_vectors_hnsw_halfvec2560;

-- 2. Stale rows lose the slice: it can no longer be recomputed (the source
-- column is about to go NULL), and an old-model slice next to no vector is
-- a lie the matryoshka arm would read.
UPDATE chunks_vectors SET vector_1024 = NULL WHERE embedding IS NULL;

-- 3. Retype embedding back to vector(4096) USING NULL, then rename back to
-- the pre-025 name (skipped when the column never carried data).
DO $$
DECLARE
    col_type TEXT;
BEGIN
    SELECT format_type(a.atttypid, a.atttypmod) INTO col_type
    FROM pg_attribute a
    WHERE a.attrelid = 'public.chunks_vectors'::regclass
      AND a.attname = 'embedding';
    IF col_type = 'halfvec(2560)' THEN
        ALTER TABLE chunks_vectors
            ALTER COLUMN embedding TYPE vector(4096) USING NULL;
        ALTER TABLE chunks_vectors RENAME COLUMN embedding TO vector;
    END IF;
END $$;

-- 4. Restore the pre-025 embedding_model default.
ALTER TABLE chunks_vectors
    ALTER COLUMN embedding_model SET DEFAULT 'nomic-embed-text';

-- 5. Coding columns back to vector(4096) USING NULL (same one-way data
-- caveat as the main column).
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
        IF col_type = 'halfvec(2560)' THEN
            EXECUTE format(
                'ALTER TABLE public.%I ALTER COLUMN %I TYPE vector(4096) USING NULL',
                tables[i], cols[i]);
        END IF;
    END LOOP;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Coding vector rollback skipped: %', SQLERRM;
END $$;
