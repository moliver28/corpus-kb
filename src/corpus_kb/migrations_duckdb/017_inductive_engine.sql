-- ============================================================================
-- Migration 017: inductive engine operational state (DuckDB mirror)
-- ============================================================================
-- Pair of migrations/017_inductive_engine.sql. Three engine-owned tables,
-- FK-free by design (plain BIGINT unit references on both engines).
-- Translation: UUID -> VARCHAR, vector(1024) -> FLOAT[1024], BIGINT
-- IDENTITY -> sequence default, jsonb -> JSON. RLS block dropped.
-- ============================================================================

-- 1. research_observations: one atomic observation per (unit, run)
CREATE SEQUENCE IF NOT EXISTS research_observations_observation_id_seq START 1;

CREATE TABLE IF NOT EXISTS research_observations (
    observation_id BIGINT PRIMARY KEY DEFAULT nextval('research_observations_observation_id_seq'),
    tenant_id VARCHAR NOT NULL,
    unit_id BIGINT NOT NULL,
    run_id VARCHAR,
    summary TEXT NOT NULL,
    question_dependent BOOLEAN NOT NULL DEFAULT FALSE,
    prompt_id TEXT NOT NULL,
    model TEXT NOT NULL,
    temperature REAL NOT NULL DEFAULT 0,
    parse_fallback BOOLEAN NOT NULL DEFAULT FALSE,
    embedding FLOAT[1024],
    embedding_model TEXT,
    model_revision TEXT,
    dimensions INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, unit_id, run_id)
);

-- 2. research_proposed_codes: proposed_code log with human-gate status
CREATE SEQUENCE IF NOT EXISTS research_proposed_codes_proposed_id_seq START 1;

CREATE TABLE IF NOT EXISTS research_proposed_codes (
    proposed_id BIGINT PRIMARY KEY DEFAULT nextval('research_proposed_codes_proposed_id_seq'),
    tenant_id VARCHAR NOT NULL,
    cluster_id TEXT NOT NULL,
    run_id VARCHAR NOT NULL,
    batch_no INT NOT NULL DEFAULT 0,
    suggested_label TEXT,
    centroid FLOAT[1024],
    centroid_snapshot JSON NOT NULL DEFAULT '[]',
    member_unit_ids BIGINT[] NOT NULL,
    n_members INT NOT NULL,
    dbcv_relative_validity REAL,
    status TEXT NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed','promoted','rejected','duplicate_blocked')),
    block_reason TEXT,
    promoted_version_id VARCHAR,
    promoted_code_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, cluster_id, run_id)
);

CREATE INDEX IF NOT EXISTS idx_research_proposed_codes_status
    ON research_proposed_codes(tenant_id, status);

-- 3. research_noise_queue: HDBSCAN noise bucket (manual review, never dropped)
CREATE SEQUENCE IF NOT EXISTS research_noise_queue_noise_id_seq START 1;

CREATE TABLE IF NOT EXISTS research_noise_queue (
    noise_id BIGINT PRIMARY KEY DEFAULT nextval('research_noise_queue_noise_id_seq'),
    tenant_id VARCHAR NOT NULL,
    unit_id BIGINT NOT NULL,
    run_id VARCHAR NOT NULL,
    cluster_label INT NOT NULL DEFAULT -1,
    probability REAL,
    entropy REAL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','resolved')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, unit_id, run_id)
);

CREATE INDEX IF NOT EXISTS idx_research_noise_queue_status
    ON research_noise_queue(tenant_id, status);
