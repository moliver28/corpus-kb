-- ============================================================================
-- Migration 018: keyword synthesis hit records (DuckDB mirror)
-- ============================================================================
-- Pair of migrations/018_keyword_synthesis.sql. One synthesis-owned table;
-- FK-free by the 017 precedent. Translation: UUID -> VARCHAR, BIGINT
-- IDENTITY -> sequence default. RLS block dropped.
-- ============================================================================

CREATE SEQUENCE IF NOT EXISTS research_keyword_hits_hit_id_seq START 1;

CREATE TABLE IF NOT EXISTS research_keyword_hits (
    hit_id BIGINT PRIMARY KEY DEFAULT nextval('research_keyword_hits_hit_id_seq'),
    tenant_id VARCHAR NOT NULL,
    cb_version_id VARCHAR,
    code_id TEXT NOT NULL,
    keyword TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'positive'
        CHECK (kind IN ('positive','exclusion')),
    unit_id BIGINT NOT NULL,
    hit_location TEXT NOT NULL
        CHECK (hit_location IN ('answer','question','both')),
    run_id VARCHAR,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, cb_version_id, code_id, kind, keyword, unit_id)
);

CREATE INDEX IF NOT EXISTS idx_research_keyword_hits_code
    ON research_keyword_hits(tenant_id, cb_version_id, code_id);
CREATE INDEX IF NOT EXISTS idx_research_keyword_hits_unit
    ON research_keyword_hits(tenant_id, unit_id);
