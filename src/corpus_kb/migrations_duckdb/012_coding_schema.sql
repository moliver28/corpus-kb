-- ============================================================================
-- Migration 012: coding-domain schema (DuckDB mirror, 12 tables)
-- ============================================================================
-- Pair of migrations/012_coding_schema.sql. Translation notes:
--   * UUID -> VARCHAR; jsonb -> JSON; unbounded numeric -> DOUBLE (the
--     mapping the DuckDB postgres scanner produces; W0 finding).
--   * Vector columns ship in the migration-025 end state: FLOAT[2560]
--     (Postgres walks vector(4096) in 012, then halfvec(2560) in 025).
--     answer_ctx_vectors.vector is nullable here for the same reason:
--     migration 025 drops its NOT NULL to let stale rows exist.
--   * chunk_signals carries its end-state primary key
--     (chunk_id, code_id, signal, tenant_id) directly: migration 015 removes
--     `detail` from the Postgres key, and DuckDB cannot alter a primary key.
--   * The three append-only triggers and all policies are dropped; the
--     gateway enforces both (bodies kept as comments below).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. code_registry: master table for each code
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS code_registry (
    code_id VARCHAR NOT NULL,
    tenant_id VARCHAR NOT NULL,
    codebook_version_id VARCHAR NOT NULL,
    name TEXT NOT NULL,
    brief_definition TEXT NOT NULL,
    full_definition TEXT,
    inclusion_criteria TEXT NOT NULL,
    exclusion_criteria TEXT NOT NULL,
    examples JSON NOT NULL DEFAULT '[]',
    theory JSON NOT NULL DEFAULT '{}',
    actor_scope TEXT,
    is_provisional BOOLEAN NOT NULL DEFAULT FALSE,
    pool_floor DOUBLE,
    residual_floor DOUBLE,
    prototype_vectors JSON,
    definition_vector FLOAT[2560],
    probe_vector FLOAT[2560],
    superseded_by TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (code_id, codebook_version_id, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_code_registry_tenant ON code_registry(tenant_id);
CREATE INDEX IF NOT EXISTS idx_code_registry_version ON code_registry(codebook_version_id);

-- ----------------------------------------------------------------------------
-- 2. codebook_versions: version control and SHA256 hash for codebook changes
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS codebook_versions (
    version_id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR NOT NULL,
    label TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    diff JSON,
    paradigm TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_codebook_versions_tenant ON codebook_versions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_codebook_versions_sha256 ON codebook_versions(sha256);

-- ----------------------------------------------------------------------------
-- 3. code_keywords: inclusion/exclusion keywords with keyness statistics
-- ----------------------------------------------------------------------------
-- The Postgres partial expression index uq_inclusion_keyword
-- (UNIQUE (tenant_id, lower(keyword)) WHERE kind='inclusion' AND
-- status='active') is dropped: the gateway enforces the rule.

CREATE TABLE IF NOT EXISTS code_keywords (
    keyword TEXT NOT NULL,
    code_id VARCHAR NOT NULL,
    tenant_id VARCHAR NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('inclusion','exclusion')),
    source TEXT NOT NULL,
    ll DOUBLE,
    log_ratio DOUBLE,
    dispersion INT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','retired')),
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (keyword, code_id, kind, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_code_keywords_code ON code_keywords(code_id);
CREATE INDEX IF NOT EXISTS idx_code_keywords_tenant ON code_keywords(tenant_id);

-- ----------------------------------------------------------------------------
-- 4. chunk_signals: pooling signals (end-state key, migration 015 shape)
-- ----------------------------------------------------------------------------
-- Postgres trigger chunk_signals_append_only_trigger (BEFORE UPDATE OR
-- DELETE, raised 'chunk_signals is append-only: UPDATE and DELETE are not
-- permitted'). Dropped; the gateway blocks UPDATE/DELETE on this table.

CREATE TABLE IF NOT EXISTS chunk_signals (
    chunk_id VARCHAR NOT NULL,
    code_id VARCHAR NOT NULL,
    tenant_id VARCHAR NOT NULL,
    signal TEXT NOT NULL
        CHECK (signal IN ('keyword','vector','vector_ctx','qa_chain','centroid','residual_sweep')),
    score DOUBLE,
    detail TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (chunk_id, code_id, signal, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_chunk_signals_chunk ON chunk_signals(chunk_id);
CREATE INDEX IF NOT EXISTS idx_chunk_signals_code ON chunk_signals(code_id);
CREATE INDEX IF NOT EXISTS idx_chunk_signals_tenant ON chunk_signals(tenant_id);

-- ----------------------------------------------------------------------------
-- 5. chunk_keyword_hits: normalized word counts per keyword
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS chunk_keyword_hits (
    chunk_id VARCHAR NOT NULL,
    keyword TEXT NOT NULL,
    code_id VARCHAR NOT NULL,
    tenant_id VARCHAR NOT NULL,
    kind TEXT NOT NULL,
    n_hits INT NOT NULL,
    PRIMARY KEY (chunk_id, keyword, code_id, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_chunk_keyword_hits_chunk ON chunk_keyword_hits(chunk_id);
CREATE INDEX IF NOT EXISTS idx_chunk_keyword_hits_tenant ON chunk_keyword_hits(tenant_id);

-- ----------------------------------------------------------------------------
-- 6. answer_ctx_vectors: question-prepend or context-pooled probe vectors
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS answer_ctx_vectors (
    chunk_id VARCHAR NOT NULL,
    tenant_id VARCHAR NOT NULL,
    -- Postgres 012 had vector(4096) NOT NULL; the 025 regime is a nullable
    -- halfvec(2560) so stale rows can exist.
    vector FLOAT[2560],
    ctx_source TEXT NOT NULL DEFAULT 'question-prepend',
    built_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (chunk_id, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_answer_ctx_vectors_tenant ON answer_ctx_vectors(tenant_id);

-- ----------------------------------------------------------------------------
-- 7. batch_status: coding batch tracking
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS batch_status (
    batch_id TEXT NOT NULL,
    tenant_id VARCHAR NOT NULL,
    family TEXT,
    n_cells INT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending','in_progress','done','parked','failed')),
    attempts INT NOT NULL DEFAULT 0,
    manifest JSON,
    updated_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (batch_id, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_batch_status_tenant ON batch_status(tenant_id);
CREATE INDEX IF NOT EXISTS idx_batch_status_state ON batch_status(state);

-- ----------------------------------------------------------------------------
-- 8. chunk_codes: coding decisions with rationale + confidence
-- ----------------------------------------------------------------------------
-- Postgres trigger chunk_codes_append_only_trigger (BEFORE UPDATE OR DELETE,
-- raised 'chunk_codes is append-only: UPDATE and DELETE are not permitted').
-- Dropped; the gateway blocks UPDATE/DELETE on this table.

CREATE TABLE IF NOT EXISTS chunk_codes (
    chunk_id VARCHAR NOT NULL,
    code_id VARCHAR NOT NULL,
    tenant_id VARCHAR NOT NULL,
    pass INT NOT NULL DEFAULT 1,
    batch_id TEXT,
    coder TEXT NOT NULL,
    model TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('assign','reject')),
    confidence DOUBLE NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    tier_signals JSON NOT NULL DEFAULT '{}',
    rationale TEXT,
    rationale_vector FLOAT[2560],
    evidence_quote TEXT,
    qa_context_used BOOLEAN NOT NULL DEFAULT FALSE,
    route TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    CHECK (decision <> 'assign' OR evidence_quote IS NOT NULL),
    PRIMARY KEY (chunk_id, code_id, coder, pass, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_chunk_codes_chunk ON chunk_codes(chunk_id);
CREATE INDEX IF NOT EXISTS idx_chunk_codes_code ON chunk_codes(code_id);
CREATE INDEX IF NOT EXISTS idx_chunk_codes_tenant ON chunk_codes(tenant_id);
CREATE INDEX IF NOT EXISTS idx_chunk_codes_batch ON chunk_codes(batch_id);
CREATE INDEX IF NOT EXISTS idx_chunk_codes_coder ON chunk_codes(coder);

-- ----------------------------------------------------------------------------
-- 9. final_codes: routing outcomes
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS final_codes (
    chunk_id VARCHAR NOT NULL,
    code_id VARCHAR NOT NULL,
    tenant_id VARCHAR NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('accepted','rejected','manual_review')),
    route TEXT NOT NULL,
    confidence DOUBLE NOT NULL,
    decided_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (chunk_id, code_id, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_final_codes_chunk ON final_codes(chunk_id);
CREATE INDEX IF NOT EXISTS idx_final_codes_code ON final_codes(code_id);
CREATE INDEX IF NOT EXISTS idx_final_codes_tenant ON final_codes(tenant_id);

-- ----------------------------------------------------------------------------
-- 10. chunk_status: per-chunk coding state
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS chunk_status (
    chunk_id VARCHAR NOT NULL,
    tenant_id VARCHAR NOT NULL,
    doc_id VARCHAR NOT NULL,
    speaker_role TEXT,
    in_any_pool BOOLEAN NOT NULL DEFAULT FALSE,
    n_codes_final INT NOT NULL DEFAULT 0,
    disposition TEXT NOT NULL DEFAULT 'pending'
        CHECK (disposition IN ('pending','coded','no_code_applies','manual_review')),
    PRIMARY KEY (chunk_id, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_chunk_status_tenant ON chunk_status(tenant_id);
CREATE INDEX IF NOT EXISTS idx_chunk_status_disposition ON chunk_status(disposition);

-- ----------------------------------------------------------------------------
-- 11. doc_extraction_audit: exhaustiveness tracking
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS doc_extraction_audit (
    doc_id VARCHAR NOT NULL,
    tenant_id VARCHAR NOT NULL,
    sweep_n INT NOT NULL,
    miss_rate_point DOUBLE,
    miss_rate_ub95 DOUBLE,
    exhausted BOOLEAN NOT NULL DEFAULT FALSE,
    audited_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (doc_id, sweep_n, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_doc_extraction_audit_tenant ON doc_extraction_audit(tenant_id);
CREATE INDEX IF NOT EXISTS idx_doc_extraction_audit_doc ON doc_extraction_audit(doc_id);

-- ----------------------------------------------------------------------------
-- 12. coding_gate_decisions: durable human-gate audit trail
-- ----------------------------------------------------------------------------
-- Postgres trigger coding_gate_decisions_append_only_trigger (BEFORE UPDATE
-- OR DELETE, raised 'coding_gate_decisions is append-only: UPDATE and DELETE
-- are not permitted'). Dropped; the gateway blocks UPDATE/DELETE here.

CREATE TABLE IF NOT EXISTS coding_gate_decisions (
    gate_id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR NOT NULL,
    phase TEXT NOT NULL,
    subject TEXT NOT NULL,
    verdict JSON NOT NULL,
    approved_by TEXT NOT NULL,
    human_reply_verbatim TEXT NOT NULL,
    "at" TIMESTAMPTZ NOT NULL DEFAULT now()
    -- "at" is quoted: DuckDB reserves the word, Postgres does not.
);

CREATE INDEX IF NOT EXISTS idx_coding_gate_decisions_tenant ON coding_gate_decisions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_coding_gate_decisions_phase ON coding_gate_decisions(phase);
