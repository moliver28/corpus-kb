-- ============================================================================
-- Migration 018: Keyword synthesis hit records (todo 17, v5 §13)
-- ============================================================================
-- Unit-grain keyword HIT records for the governance report's hit_location
-- populations. The synthesized keyword SETS themselves are codebook
-- governance state: they ride the CodebookVersion.KeywordSetUpdated event
-- and are projected onto code_registry.theory.keywords (version-scoped
-- JSONB) + the Wave-1 code_keywords table (existing projector path), so no
-- parallel keyword-set table exists here.
--
-- research_keyword_hits is SYNTHESIS-OWNED operational state (same ownership
-- class as migration 017's tables: written by the keyword synthesis run
-- under the tenant GUC, idempotent ON CONFLICT, NOT projection-owned —
-- nothing in it is event-derivable). FK-FREE by the 017 precedent: a FK
-- into the 016 read models would break rollback_016 (plain DROP TABLE) and
-- the rebuild drop-set; every lookup joins research_units.
--
-- hit_location semantics (v5 §13): 'answer' hits count as explicit
-- evidence; 'question' hits are candidate generators gated by stance +
-- information gain; 'both' carries the answer evidence.

-- ----------------------------------------------------------------------------
-- 1. research_keyword_hits - one row per (code, keyword, unit) hit
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS research_keyword_hits (
    hit_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id UUID NOT NULL,
    cb_version_id UUID,
    code_id TEXT NOT NULL,
    keyword TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'positive'
        CHECK (kind IN ('positive','exclusion')),
    unit_id BIGINT NOT NULL,
    hit_location TEXT NOT NULL
        CHECK (hit_location IN ('answer','question','both')),
    run_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, cb_version_id, code_id, kind, keyword, unit_id)
);

CREATE INDEX IF NOT EXISTS idx_research_keyword_hits_code
    ON research_keyword_hits(tenant_id, cb_version_id, code_id);
CREATE INDEX IF NOT EXISTS idx_research_keyword_hits_unit
    ON research_keyword_hits(tenant_id, unit_id);

-- ----------------------------------------------------------------------------
-- 2. Row-level security (016 convention: DROP POLICY IF EXISTS + FORCE,
--    nested per-table so a re-apply after rollback warns and continues)
-- ----------------------------------------------------------------------------

DO $$
BEGIN
    ALTER TABLE research_keyword_hits ENABLE ROW LEVEL SECURITY;
    ALTER TABLE research_keyword_hits FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'RLS setup failed for research_keyword_hits: %', SQLERRM;
END $$;

DO $$
BEGIN
    DROP POLICY IF EXISTS research_keyword_hits_tenant_isolation ON research_keyword_hits;
    CREATE POLICY research_keyword_hits_tenant_isolation ON research_keyword_hits
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID);
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Policy research_keyword_hits_tenant_isolation: %', SQLERRM;
END $$;
