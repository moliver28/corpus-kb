-- ============================================================================
-- Migration 017: Inductive engine operational state (todo 15, v5 §9)
-- ============================================================================
-- Three ENGINE-OWNED operational tables (same ownership class as
-- research_transcript_text + ingested_files: written by the inductive run
-- under the tenant GUC, NOT projection-owned read models — none of their
-- state is event-derivable, so they are excluded from the F6 rebuild
-- drop-set). The run's EVENT-SOURCED trail (determinism manifest, ISR per
-- batch, DBCV, proposal summaries) rides CodingRun checkpoints as before.
--
--   research_observations    atomic-observation summaries + their summary-
--                            space embeddings (temp 0; prompt id + model
--                            logged per row; question_dependent flag).
--   research_proposed_codes  the proposed_code log: cluster id, centroid
--                            snapshot (PROVENANCE ONLY — summary-embedding
--                            space, never a scoring prototype; r7), member
--                            unit_ids, proposing batch, human-gate status.
--   research_noise_queue     HDBSCAN noise bucket -> manual-review queue
--                            (never discarded; rows persist until resolved).
--
-- FK-FREE BY DESIGN: all three tables carry plain BIGINT unit references —
-- they are engine-owned operational state, and FKs into the 016 read models
-- would break rollback_016 (plain DROP TABLE) and the rebuild drop-set
-- (TRUNCATE ... research_units) that the es-foundation/rebuild proofs drive
-- directly. Orphan rows are harmless: every lookup joins research_units.
--
-- Vector note: observations carry vector(1024) with the standard model
-- metadata. No embedding_256 mirror and no HNSW here — the clustering load
-- is a bounded full-table read per project, not an ANN access pattern (the
-- 016 one-index-per-access-pattern rule).
--
-- event_store/snapshot_store (lib-owned) are NEVER touched by this migration.

-- ----------------------------------------------------------------------------
-- 1. research_observations - one atomic observation per (unit, run)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS research_observations (
    observation_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id UUID NOT NULL,
    unit_id BIGINT NOT NULL,
    run_id UUID,
    summary TEXT NOT NULL,
    question_dependent BOOLEAN NOT NULL DEFAULT FALSE,
    prompt_id TEXT NOT NULL,
    model TEXT NOT NULL,
    temperature REAL NOT NULL DEFAULT 0,
    parse_fallback BOOLEAN NOT NULL DEFAULT FALSE,
    embedding vector(1024),
    embedding_model TEXT,
    model_revision TEXT,
    dimensions INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, unit_id, run_id)
);

-- ----------------------------------------------------------------------------
-- 2. research_proposed_codes - proposed_code log with human-gate status
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS research_proposed_codes (
    proposed_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id UUID NOT NULL,
    cluster_id TEXT NOT NULL,
    run_id UUID NOT NULL,
    batch_no INT NOT NULL DEFAULT 0,
    suggested_label TEXT,
    centroid vector(1024),
    centroid_snapshot JSONB NOT NULL DEFAULT '[]',
    member_unit_ids BIGINT[] NOT NULL,
    n_members INT NOT NULL,
    dbcv_relative_validity REAL,
    status TEXT NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed','promoted','rejected','duplicate_blocked')),
    block_reason TEXT,
    promoted_version_id UUID,
    promoted_code_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, cluster_id, run_id)
);

CREATE INDEX IF NOT EXISTS idx_research_proposed_codes_status
    ON research_proposed_codes(tenant_id, status);

-- ----------------------------------------------------------------------------
-- 3. research_noise_queue - HDBSCAN noise bucket (manual review, never dropped)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS research_noise_queue (
    noise_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id UUID NOT NULL,
    unit_id BIGINT NOT NULL,
    run_id UUID NOT NULL,
    cluster_label INT NOT NULL DEFAULT -1,
    probability REAL,
    entropy REAL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','resolved')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, unit_id, run_id)
);

CREATE INDEX IF NOT EXISTS idx_research_noise_queue_status
    ON research_noise_queue(tenant_id, status);

-- ----------------------------------------------------------------------------
-- 4. RLS policies + FORCE on every new table (012/013/016 convention)
-- ----------------------------------------------------------------------------

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'research_observations','research_proposed_codes','research_noise_queue'
    ] LOOP
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
