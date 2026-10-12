-- ============================================================================
-- Migration 021: U48/P6 side-by-side shadow hierarchy index
-- ============================================================================
-- The multi-granularity token hierarchy (child 128 / chunk 512 / parent
-- 2048 tokens, 64-token overlap) as a SIDE-BY-SIDE SHADOW index:
--
--   * v1 `chunks` / `chunks_vectors` stay the LIVE search corpus and are
--     NOT touched by this migration (or by the shadow ingest that feeds
--     these tables). Cutover is a later, separately-approved operation —
--     deliberately NOT part of this migration.
--   * `shadow_chunks` carries the three levels (`level` IN child/chunk/
--     parent) with self-referencing `parent_id` links: every child points
--     at its chunk-level parent, every chunk at its document-level parent.
--     Main-path child rows also record the containing v1 chunk id in
--     metadata ('v1_chunk_id') so query-time parent expansion can map a
--     live hit onto its shadow context without touching the live path.
--   * `shadow_vectors` mirrors the research-grain vector strategy (migration
--     016): column vector(1024) — G1-promotable embedders MUST emit exactly
--     1024 dims (native or MRL-sliced from >=1024; never padded; models
--     below 1024 dims are honestly skipped by the writer) — indexed through
--     the halfvec CAST EXPRESSION ((embedding::halfvec(1024))
--     halfvec_cosine_ops), the committed U46 halfvec-at-the-index-layer
--     decision. Requires pgvector >= 0.7 for the index; on older servers
--     the DO block skips the index with a warning and the table still
--     works (exact scans only).
--   * Rows are a DERIVED, rebuildable artifact: the shadow ingest deletes
--     + reinserts a document's rows wholesale (no v1-style hash dedup).
--   * RLS mirrors 016/019/020: ENABLE + FORCE + tenant GUC policy per
--     table, so a shadow read can never cross tenants.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS + DROP POLICY IF EXISTS before
-- CREATE POLICY, matching the house migration convention.
-- ============================================================================

SET app.current_tenant_id = '00000000-0000-0000-0000-000000000001';

-- ----------------------------------------------------------------------------
-- 1. shadow_chunks — the three-level text hierarchy
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS shadow_chunks (
    shadow_chunk_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    doc_id UUID NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    level TEXT NOT NULL CHECK (level IN ('child','chunk','parent')),
    seq INT NOT NULL,
    parent_id UUID REFERENCES shadow_chunks(shadow_chunk_id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    token_count INT NOT NULL,
    start_char INT,
    end_char INT,
    boundary TEXT,
    -- content hash of the SOURCE DOCUMENT (rebuild trigger), not of this row
    source_hash CHAR(64),
    path TEXT NOT NULL DEFAULT 'main',
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, doc_id, path, level, seq)
);

CREATE INDEX IF NOT EXISTS idx_shadow_chunks_parent
    ON shadow_chunks(tenant_id, parent_id);
CREATE INDEX IF NOT EXISTS idx_shadow_chunks_doc_level
    ON shadow_chunks(tenant_id, doc_id, path, level);

-- ----------------------------------------------------------------------------
-- 2. shadow_vectors — halfvec-indexed embeddings for the retrieval levels
-- (child 128 + chunk 512; the parent 2048 level is context-only and is
-- reached through parent_id, per the small-to-big contract)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS shadow_vectors (
    shadow_chunk_id UUID PRIMARY KEY REFERENCES shadow_chunks(shadow_chunk_id)
        ON DELETE CASCADE,
    tenant_id UUID NOT NULL,
    embedding vector(1024),
    embedding_model TEXT NOT NULL DEFAULT 'nomic-embed-text',
    dimensions INT,
    embedded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT shadow_vector_not_null CHECK (embedding IS NOT NULL)
);

-- ----------------------------------------------------------------------------
-- 3. RLS: tenant isolation (migration 016 policy pattern, ENABLE + FORCE)
-- ----------------------------------------------------------------------------

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['shadow_chunks', 'shadow_vectors'] LOOP
        BEGIN
            EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
            EXECUTE format('DROP POLICY IF EXISTS %I_tenant_isolation ON %I', t, t);
            EXECUTE format(
                'CREATE POLICY %I_tenant_isolation ON %I '
                'USING (tenant_id = NULLIF(current_setting(''app.current_tenant_id'', true), '''')::UUID) '
                'WITH CHECK (tenant_id = NULLIF(current_setting(''app.current_tenant_id'', true), '''')::UUID)',
                t, t);
            EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING 'RLS setup failed for %: %', t, SQLERRM;
        END;
    END LOOP;
END $$;

-- ----------------------------------------------------------------------------
-- 4. Vector index: halfvec CAST-EXPRESSION HNSW (migration 016 pattern).
-- Requires pgvector >= 0.7 (halfvec opclass); older servers skip the index
-- with a warning — the shadow stays queryable via exact scans.
-- ----------------------------------------------------------------------------

DO $$
BEGIN
    EXECUTE 'CREATE INDEX IF NOT EXISTS idx_shadow_vectors_hnsw_halfvec '
            'ON shadow_vectors USING hnsw ((embedding::halfvec(1024)) halfvec_cosine_ops) '
            'WITH (m = 16, ef_construction = 200)';
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Skipping idx_shadow_vectors_hnsw_halfvec: %', SQLERRM;
END $$;
