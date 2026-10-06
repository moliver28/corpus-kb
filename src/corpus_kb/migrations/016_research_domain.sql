-- ============================================================================
-- Migration 016: Research domain read models + repaired projection foundation
-- ============================================================================
-- RECONCILIATION with ported 012-015 (v5 spec §4 vs Wave-1 tables):
--   * code / codebook legs REUSE 012's code_registry and codebook_versions —
--     the research code_projector projects CodebookVersion aggregate events
--     into them (ON CONFLICT by their existing keys). No new tables.
--   * v5 §4's chunk-keyed coding tables (chunk_codes, chunk_signals,
--     final_codes, coding_gate_decisions) stay LEGACY-PATH-OWNED: their grain
--     is the legacy chunks.uuid pipeline, their append-only triggers are
--     KEPT, and the frozen direct-write path keeps writing them (IS-4).
--   * the genuinely-new research grain (speaker turns / exchanges /
--     assignments / signals / runs / reviews) gets NEW projection-owned
--     tables below: idempotent ON CONFLICT keys, NO append-only triggers.
--   * documents is EXTENDED with research columns (project_id, title,
--     source_path, source_hash, parser_name, parser_version, doc_version);
--     transcripts ride the same Document aggregate + documents read table.
-- Append-only triggers: none of the tables the projections now own
-- (documents, code_registry, codebook_versions, research_*) ever had them,
-- so nothing is dropped here; the legacy append-only triggers on chunk_codes
-- and coding_gate_decisions remain (those tables stay direct-write).
-- event_store/snapshot_store (lib-owned stored_events/stored_snapshots) are
-- NEVER touched by this migration.
--
-- VECTOR STRATEGY (pgvector 0.8.3 verified on the live local PostgreSQL 16):
--   * primary columns typed vector(1024) — G1-promotable embedders MUST emit
--     exactly 1024 dims (native or MRL-sliced from >=1024); never padded.
--   * maintained analytics column embedding_256 vector(256), kept equal to
--     l2_normalize(subvector(embedding, 1, 256)) by the writing projection
--     (migration-010 precedent).
--   * ONE index per access pattern:
--       - RETRIEVAL: HNSW on the halfvec cast expression
--         ((embedding::halfvec(1024)) halfvec_cosine_ops); probe pinned as
--         embedding::halfvec(1024) <=> $1::halfvec(1024). ~2x memory cut.
--       - ANALYTICS: HNSW on embedding_256 vector_cosine_ops; probe
--         embedding_256 <=> $1::vector(256).
--       - RE-RANK: exact vector(1024) comparison over candidate sets —
--         deliberately UNINDEXED (re-rank is an exact computation over a
--         small candidate list; an HNSW here would only burn memory).
--   * every vector row carries embedding_model + model_revision + dimensions.
--
-- CHECKPOINT MECHANICS: projection_checkpoints gains last_sequence BIGINT —
-- the eventsourcing library's notification_id bigserial over stored_events
-- (verified: 9.5.x stored_events = originator_id/originator_version/topic/
-- state/notification_id). Projections persist the GLOBAL monotonic position,
-- never created_at comparisons.
-- ============================================================================

SET app.current_tenant_id = '00000000-0000-0000-0000-000000000001';

-- ----------------------------------------------------------------------------
-- 1. Checkpoint sequence column
-- ----------------------------------------------------------------------------

ALTER TABLE projection_checkpoints ADD COLUMN IF NOT EXISTS last_sequence BIGINT;

-- ----------------------------------------------------------------------------
-- 2. documents: research transcript columns (ALTER/EXTEND — no new table)
-- ----------------------------------------------------------------------------

ALTER TABLE documents ADD COLUMN IF NOT EXISTS project_id UUID;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS title TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_path TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_hash CHAR(64);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS parser_name TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS parser_version TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_version INT NOT NULL DEFAULT 1;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS parse_quality TEXT;

CREATE INDEX IF NOT EXISTS idx_documents_project ON documents(project_id)
    WHERE project_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_documents_source_hash ON documents(source_hash)
    WHERE source_hash IS NOT NULL;

-- ----------------------------------------------------------------------------
-- 3. research_projects (read model derived from events; no aggregate of its own)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS research_projects (
    project_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ----------------------------------------------------------------------------
-- 4. research_speakers
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS research_speakers (
    speaker_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    doc_id UUID NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    raw_label TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'unknown'
        CHECK (role IN ('moderator','participant','observer','unknown')),
    pseudonym TEXT,
    role_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    role_basis TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, doc_id, raw_label)
);

-- ----------------------------------------------------------------------------
-- 5. research_transcript_text — IMMUTABLE content-addressed turn store
-- (ingest-owned, NOT projection-owned: a read model cannot be rebuilt from a
-- reference, so this table is EXCLUDED from the rebuild drop-set)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS research_transcript_text (
    tenant_id UUID NOT NULL,
    text_sha256 CHAR(64) NOT NULL,
    text TEXT NOT NULL,
    media_type TEXT NOT NULL DEFAULT 'text',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, text_sha256)
);

-- ----------------------------------------------------------------------------
-- 6. research_units — THE CODING UNIT (speaker turn)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS research_units (
    unit_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id UUID NOT NULL,
    doc_id UUID NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    project_id UUID,
    speaker_id UUID REFERENCES research_speakers(speaker_id) ON DELETE SET NULL,
    seq INT NOT NULL,
    t_start REAL,
    t_end REAL,
    text TEXT NOT NULL,
    text_sha256 CHAR(64) NOT NULL,
    exchange_id BIGINT,
    role_in_exchange TEXT,
    reply_to_unit_id BIGINT,
    derived BOOLEAN NOT NULL DEFAULT FALSE,
    is_codable BOOLEAN NOT NULL DEFAULT TRUE,
    token_count INT,
    turn_type TEXT,
    embedding vector(1024),
    embedding_256 vector(256),
    embedding_model TEXT,
    model_revision TEXT,
    dimensions INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, doc_id, seq)
);

-- ----------------------------------------------------------------------------
-- 7. research_exchanges — moderator question(s) -> participant answer(s)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS research_exchanges (
    exchange_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id UUID NOT NULL,
    doc_id UUID NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    seq INT NOT NULL,
    q_unit_ids BIGINT[] NOT NULL DEFAULT '{}',
    a_unit_ids BIGINT[] NOT NULL DEFAULT '{}',
    topic_id INT,
    link_method TEXT NOT NULL DEFAULT 'adjacency'
        CHECK (link_method IN ('adjacency','cross_ref','guide_match','manual')),
    link_confidence TEXT,
    link_score REAL,
    question_text TEXT,
    qa_text TEXT,
    stance TEXT CHECK (stance IN ('affirm','deny','partial','deflect') OR stance IS NULL),
    term_origin TEXT CHECK (term_origin IN ('participant','moderator','both') OR term_origin IS NULL),
    reviewed BOOLEAN NOT NULL DEFAULT FALSE,
    embedding vector(1024),
    embedding_256 vector(256),
    embedding_model TEXT,
    model_revision TEXT,
    dimensions INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, doc_id, seq)
);

-- ----------------------------------------------------------------------------
-- 8. research_assignments — per-unit coding decisions (event-sourced)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS research_assignments (
    assignment_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id UUID NOT NULL,
    assignment_aggregate_id UUID NOT NULL,
    unit_id BIGINT NOT NULL REFERENCES research_units(unit_id) ON DELETE CASCADE,
    code_id TEXT NOT NULL,
    cb_version_id UUID,
    run_id UUID,
    sim_answer REAL,
    sim_qa REAL,
    sim_q REAL,
    evidence_basis TEXT CHECK (evidence_basis IN ('explicit_in_answer','question_dependent') OR evidence_basis IS NULL),
    stance TEXT,
    term_origin TEXT,
    rationale TEXT,
    confidence TEXT CHECK (confidence IN ('high','medium','low') OR confidence IS NULL),
    tier_fired INT,
    status TEXT NOT NULL DEFAULT 'auto'
        CHECK (status IN ('auto','review','confirmed','overridden')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, assignment_aggregate_id)
);

CREATE INDEX IF NOT EXISTS idx_research_assignments_unit ON research_assignments(tenant_id, unit_id);
CREATE INDEX IF NOT EXISTS idx_research_assignments_code ON research_assignments(tenant_id, code_id);
CREATE INDEX IF NOT EXISTS idx_research_assignments_status ON research_assignments(tenant_id, status);

-- ----------------------------------------------------------------------------
-- 9. research_signals — uncertainty signal rows per unit/run
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS research_signals (
    signal_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id UUID NOT NULL,
    unit_id BIGINT NOT NULL REFERENCES research_units(unit_id) ON DELETE CASCADE,
    run_id UUID,
    tier INT NOT NULL,
    token_entropy REAL,
    hedge_flag BOOLEAN,
    soft_cluster_entropy REAL,
    semantic_entropy REAL,
    n_clusters INT,
    link_score REAL,
    extra JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, unit_id, run_id, tier)
);

-- ----------------------------------------------------------------------------
-- 10. research_runs — thin CodingRun coordinator read model
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS research_runs (
    run_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    llm_name TEXT,
    llm_version TEXT,
    embed_model TEXT,
    model_revision TEXT,
    params JSONB NOT NULL DEFAULT '{}',
    state TEXT NOT NULL DEFAULT 'started'
        CHECK (state IN ('started','checkpoint','stopped')),
    checkpoint JSONB,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    stopped_at TIMESTAMPTZ
);

-- ----------------------------------------------------------------------------
-- 11. research_reviews — human decisions on assignments
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS research_reviews (
    review_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id UUID NOT NULL,
    assignment_id BIGINT NOT NULL REFERENCES research_assignments(assignment_id) ON DELETE CASCADE,
    reviewer TEXT NOT NULL,
    decision TEXT NOT NULL,
    note TEXT,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, assignment_id, reviewer)
);

-- ----------------------------------------------------------------------------
-- 12. ingested_files — INGEST-OWNED operational ledger
-- (mtime/size are not event-derivable => NOT projection-owned; EXCLUDED from
-- the rebuild drop-set; lazily reconstructed by idempotent re-scan)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ingested_files (
    tenant_id UUID NOT NULL,
    path TEXT NOT NULL,
    mtime TIMESTAMPTZ,
    size BIGINT,
    file_sha256 CHAR(64) NOT NULL,
    source_type TEXT,
    doc_id UUID,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, path)
);

-- ----------------------------------------------------------------------------
-- 13. embedding_cache — write-once, per-tenant, 1024-dim only
-- (NOT dropped on projection rebuild; projections re-link by key. The insert
-- path REJECTS zero/non-unit vectors in Python — a degraded embed call must
-- never poison the cache with a degenerate "hit".)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS embedding_cache (
    tenant_id UUID NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    model TEXT NOT NULL,
    model_revision TEXT NOT NULL,
    dim INT NOT NULL CHECK (dim = 1024),
    embedding vector(1024) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, content_sha256, model, model_revision)
);

CREATE INDEX IF NOT EXISTS idx_embedding_cache_model ON embedding_cache(tenant_id, model, model_revision);

-- ----------------------------------------------------------------------------
-- 14. RLS policies + FORCE on every new table (012/013 convention)
-- ----------------------------------------------------------------------------

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'research_projects','research_speakers','research_transcript_text',
        'research_units','research_exchanges','research_assignments',
        'research_signals','research_runs','research_reviews',
        'ingested_files','embedding_cache'
    ] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY %I_tenant_isolation ON %I '
            'USING (tenant_id = NULLIF(current_setting(''app.current_tenant_id'', true), '''')::UUID) '
            'WITH CHECK (tenant_id = NULLIF(current_setting(''app.current_tenant_id'', true), '''')::UUID)',
            t, t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    END LOOP;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'RLS setup problem: %', SQLERRM;
END $$;

-- ----------------------------------------------------------------------------
-- 15. Vector indexes (ONE per access pattern; created last — bulk replay
-- drops secondary indexes first and re-runs CREATE INDEX after load)
-- ----------------------------------------------------------------------------

DO $$
BEGIN
    EXECUTE 'CREATE INDEX IF NOT EXISTS idx_research_units_hnsw_halfvec '
            'ON research_units USING hnsw ((embedding::halfvec(1024)) halfvec_cosine_ops) '
            'WITH (m = 16, ef_construction = 200)';
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Skipping idx_research_units_hnsw_halfvec: %', SQLERRM;
END $$;

DO $$
BEGIN
    EXECUTE 'CREATE INDEX IF NOT EXISTS idx_research_units_hnsw_256 '
            'ON research_units USING hnsw (embedding_256 vector_cosine_ops) '
            'WITH (m = 16, ef_construction = 200)';
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Skipping idx_research_units_hnsw_256: %', SQLERRM;
END $$;

DO $$
BEGIN
    EXECUTE 'CREATE INDEX IF NOT EXISTS idx_research_exchanges_hnsw_halfvec '
            'ON research_exchanges USING hnsw ((embedding::halfvec(1024)) halfvec_cosine_ops) '
            'WITH (m = 16, ef_construction = 200)';
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Skipping idx_research_exchanges_hnsw_halfvec: %', SQLERRM;
END $$;

DO $$
BEGIN
    EXECUTE 'CREATE INDEX IF NOT EXISTS idx_research_exchanges_hnsw_256 '
            'ON research_exchanges USING hnsw (embedding_256 vector_cosine_ops) '
            'WITH (m = 16, ef_construction = 200)';
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Skipping idx_research_exchanges_hnsw_256: %', SQLERRM;
END $$;

CREATE INDEX IF NOT EXISTS idx_research_units_doc ON research_units(tenant_id, doc_id, seq);
CREATE INDEX IF NOT EXISTS idx_research_exchanges_doc ON research_exchanges(tenant_id, doc_id, seq);
CREATE INDEX IF NOT EXISTS idx_research_speakers_doc ON research_speakers(tenant_id, doc_id);
CREATE INDEX IF NOT EXISTS idx_research_runs_tenant ON research_runs(tenant_id);
