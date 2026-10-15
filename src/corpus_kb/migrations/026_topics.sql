-- ============================================================================
-- Migration 026: Wave 4 topic layer (BERTopic over stored embeddings)
-- ============================================================================
-- Two tenant-scoped tables for the offline `topics_build` op:
--
--   * `topics` — one row per discovered topic: the c-TF-IDF label
--     (`label`), the optional LLM label (`reduced_label`), the honest
--     chunk count (`size`), the c-TF-IDF keyword ranking (`keywords`
--     jsonb) and the full model parameter record (`params` jsonb, the
--     determinism contract: UMAP random state, min_topic_size, ...).
--   * `chunk_topics` — the per-chunk assignment: exactly one topic per
--     chunk per tenant (UNIQUE), plus BERTopic's membership probability
--     (approximate; calculate_probabilities=false, see engine.py).
--     Outlier chunks (topic -1) get no row: non-membership is honest.
--
-- Rows are a DERIVED, rebuildable artifact: topics_build deletes the
-- tenant's rows wholesale before inserting the new build (the shadow
-- index convention, migration 021). `chunk_topics.topic_id` cascades on
-- topic deletion, so the wholesale delete is one statement.
--
-- RLS mirrors 016/019/020/021: ENABLE + FORCE + tenant GUC policy per
-- table, so a topic read can never cross tenants.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS + DROP POLICY IF EXISTS before
-- CREATE POLICY, matching the house migration convention.
-- ============================================================================

SET app.current_tenant_id = '00000000-0000-0000-0000-000000000001';

-- ----------------------------------------------------------------------------
-- 1. topics — the discovered topic rows
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS topics (
    topic_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    label TEXT NOT NULL,
    reduced_label TEXT,
    size INT NOT NULL CHECK (size >= 0),
    keywords JSONB NOT NULL DEFAULT '[]',
    params JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ----------------------------------------------------------------------------
-- 2. chunk_topics — the per-chunk assignment (one topic per chunk)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS chunk_topics (
    tenant_id UUID NOT NULL,
    chunk_id UUID NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    topic_id UUID NOT NULL REFERENCES topics(topic_id) ON DELETE CASCADE,
    probability DOUBLE PRECISION NOT NULL DEFAULT 0.0
        CHECK (probability >= 0.0 AND probability <= 1.0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_chunk_topics_topic
    ON chunk_topics(tenant_id, topic_id);
CREATE INDEX IF NOT EXISTS idx_topics_tenant_size
    ON topics(tenant_id, size DESC);

-- ----------------------------------------------------------------------------
-- 3. RLS: tenant isolation (migration 021 policy pattern, ENABLE + FORCE)
-- ----------------------------------------------------------------------------

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['topics', 'chunk_topics'] LOOP
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
