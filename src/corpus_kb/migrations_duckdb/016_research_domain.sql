-- ============================================================================
-- Migration 016: research domain read models (DuckDB mirror, 11 tables)
-- ============================================================================
-- Pair of migrations/016_research_domain.sql. Translation notes:
--   * UUID -> VARCHAR; jsonb -> JSON; vector(1024) -> FLOAT[1024];
--     vector(256) -> FLOAT[256]; BIGINT IDENTITY -> sequence default.
--   * doc_version arrives in two steps (DuckDB cannot ADD COLUMN with NOT
--     NULL: add with the default, then SET NOT NULL).
--   * Partial indexes (WHERE project_id / source_hash IS NOT NULL) lose the
--     predicate; HNSW indexes and the RLS loop are dropped.
--   * FK ON DELETE CASCADE / SET NULL actions are Postgres constraint
--     clauses; the gateway enforces the delete behavior.
-- ============================================================================

-- 1. Checkpoint sequence column
ALTER TABLE projection_checkpoints ADD COLUMN IF NOT EXISTS last_sequence BIGINT;

-- 2. documents: research transcript columns
ALTER TABLE documents ADD COLUMN IF NOT EXISTS project_id VARCHAR;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS title TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_path TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_hash CHAR(64);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS parser_name TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS parser_version TEXT;
-- doc_version was created with the table in 000 (DuckDB refuses NOT NULL
-- ALTERs on referenced tables); this statement is the pair-alignment no-op.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_version INT DEFAULT 1;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS parse_quality TEXT;

CREATE INDEX IF NOT EXISTS idx_documents_project ON documents(project_id);
CREATE INDEX IF NOT EXISTS idx_documents_source_hash ON documents(source_hash);

-- 3. research_projects
CREATE TABLE IF NOT EXISTS research_projects (
    project_id VARCHAR PRIMARY KEY,
    tenant_id VARCHAR NOT NULL,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 4. research_speakers
CREATE TABLE IF NOT EXISTS research_speakers (
    speaker_id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR NOT NULL,
    doc_id VARCHAR NOT NULL REFERENCES documents(doc_id),
    -- Postgres action: ON DELETE CASCADE.
    raw_label TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'unknown'
        CHECK (role IN ('moderator','participant','observer','unknown')),
    pseudonym TEXT,
    role_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    role_basis TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, doc_id, raw_label)
);

-- 5. research_transcript_text (immutable content-addressed turn store)
CREATE TABLE IF NOT EXISTS research_transcript_text (
    tenant_id VARCHAR NOT NULL,
    text_sha256 CHAR(64) NOT NULL,
    text TEXT NOT NULL,
    media_type TEXT NOT NULL DEFAULT 'text',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, text_sha256)
);

-- 6. research_units (the coding unit: speaker turn)
CREATE SEQUENCE IF NOT EXISTS research_units_unit_id_seq START 1;

CREATE TABLE IF NOT EXISTS research_units (
    unit_id BIGINT PRIMARY KEY DEFAULT nextval('research_units_unit_id_seq'),
    tenant_id VARCHAR NOT NULL,
    doc_id VARCHAR NOT NULL REFERENCES documents(doc_id),
    -- Postgres action: ON DELETE CASCADE.
    project_id VARCHAR,
    speaker_id VARCHAR REFERENCES research_speakers(speaker_id),
    -- Postgres action: ON DELETE SET NULL.
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
    embedding FLOAT[1024],
    embedding_256 FLOAT[256],
    embedding_model TEXT,
    model_revision TEXT,
    dimensions INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, doc_id, seq)
);

-- 7. research_exchanges (moderator question(s) -> participant answer(s))
CREATE SEQUENCE IF NOT EXISTS research_exchanges_exchange_id_seq START 1;

CREATE TABLE IF NOT EXISTS research_exchanges (
    exchange_id BIGINT PRIMARY KEY DEFAULT nextval('research_exchanges_exchange_id_seq'),
    tenant_id VARCHAR NOT NULL,
    doc_id VARCHAR NOT NULL REFERENCES documents(doc_id),
    -- Postgres action: ON DELETE CASCADE.
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
    embedding FLOAT[1024],
    embedding_256 FLOAT[256],
    embedding_model TEXT,
    model_revision TEXT,
    dimensions INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, doc_id, seq)
);

-- 8. research_assignments (per-unit coding decisions)
CREATE SEQUENCE IF NOT EXISTS research_assignments_assignment_id_seq START 1;

CREATE TABLE IF NOT EXISTS research_assignments (
    assignment_id BIGINT PRIMARY KEY DEFAULT nextval('research_assignments_assignment_id_seq'),
    tenant_id VARCHAR NOT NULL,
    assignment_aggregate_id VARCHAR NOT NULL,
    unit_id BIGINT NOT NULL REFERENCES research_units(unit_id),
    -- Postgres action: ON DELETE CASCADE.
    code_id TEXT NOT NULL,
    cb_version_id VARCHAR,
    run_id VARCHAR,
    sim_answer REAL,
    sim_qa REAL,
    sim_q REAL,
    evidence_basis TEXT
        CHECK (evidence_basis IN ('explicit_in_answer','question_dependent') OR evidence_basis IS NULL),
    stance TEXT,
    term_origin TEXT,
    rationale TEXT,
    confidence TEXT CHECK (confidence IN ('high','medium','low') OR confidence IS NULL),
    tier_fired INT,
    status TEXT NOT NULL DEFAULT 'auto'
        CHECK (status IN ('auto','review','confirmed','overridden')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, assignment_aggregate_id)
);

CREATE INDEX IF NOT EXISTS idx_research_assignments_unit ON research_assignments(tenant_id, unit_id);
CREATE INDEX IF NOT EXISTS idx_research_assignments_code ON research_assignments(tenant_id, code_id);
CREATE INDEX IF NOT EXISTS idx_research_assignments_status ON research_assignments(tenant_id, status);

-- 9. research_signals (uncertainty signal rows per unit/run)
CREATE SEQUENCE IF NOT EXISTS research_signals_signal_id_seq START 1;

CREATE TABLE IF NOT EXISTS research_signals (
    signal_id BIGINT PRIMARY KEY DEFAULT nextval('research_signals_signal_id_seq'),
    tenant_id VARCHAR NOT NULL,
    unit_id BIGINT NOT NULL REFERENCES research_units(unit_id),
    -- Postgres action: ON DELETE CASCADE.
    run_id VARCHAR,
    tier INT NOT NULL,
    token_entropy REAL,
    hedge_flag BOOLEAN,
    soft_cluster_entropy REAL,
    semantic_entropy REAL,
    n_clusters INT,
    link_score REAL,
    extra JSON NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, unit_id, run_id, tier)
);

-- 10. research_runs (thin CodingRun coordinator read model)
CREATE TABLE IF NOT EXISTS research_runs (
    run_id VARCHAR PRIMARY KEY,
    tenant_id VARCHAR NOT NULL,
    llm_name TEXT,
    llm_version TEXT,
    embed_model TEXT,
    model_revision TEXT,
    params JSON NOT NULL DEFAULT '{}',
    state TEXT NOT NULL DEFAULT 'started'
        CHECK (state IN ('started','checkpoint','stopped')),
    checkpoint JSON,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    stopped_at TIMESTAMPTZ
);

-- 11. research_reviews (human decisions on assignments)
CREATE SEQUENCE IF NOT EXISTS research_reviews_review_id_seq START 1;

CREATE TABLE IF NOT EXISTS research_reviews (
    review_id BIGINT PRIMARY KEY DEFAULT nextval('research_reviews_review_id_seq'),
    tenant_id VARCHAR NOT NULL,
    assignment_id BIGINT NOT NULL REFERENCES research_assignments(assignment_id),
    -- Postgres action: ON DELETE CASCADE.
    reviewer TEXT NOT NULL,
    decision TEXT NOT NULL,
    note TEXT,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, assignment_id, reviewer)
);

-- 12. ingested_files (ingest-owned operational ledger)
CREATE TABLE IF NOT EXISTS ingested_files (
    tenant_id VARCHAR NOT NULL,
    path TEXT NOT NULL,
    mtime TIMESTAMPTZ,
    size BIGINT,
    file_sha256 CHAR(64) NOT NULL,
    source_type TEXT,
    doc_id VARCHAR,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, path)
);

-- 13. embedding_cache (write-once, per-tenant, 1024-dim only)
CREATE TABLE IF NOT EXISTS embedding_cache (
    tenant_id VARCHAR NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    model TEXT NOT NULL,
    model_revision TEXT NOT NULL,
    dim INT NOT NULL CHECK (dim = 1024),
    embedding FLOAT[1024] NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, content_sha256, model, model_revision)
);

CREATE INDEX IF NOT EXISTS idx_embedding_cache_model
    ON embedding_cache(tenant_id, model, model_revision);

-- 14. Plain indexes for the research grain (HNSW pairs dropped; DuckDB
-- defaults to exact scan).
CREATE INDEX IF NOT EXISTS idx_research_units_doc ON research_units(tenant_id, doc_id, seq);
CREATE INDEX IF NOT EXISTS idx_research_exchanges_doc ON research_exchanges(tenant_id, doc_id, seq);
CREATE INDEX IF NOT EXISTS idx_research_speakers_doc ON research_speakers(tenant_id, doc_id);
CREATE INDEX IF NOT EXISTS idx_research_runs_tenant ON research_runs(tenant_id);
