-- ============================================================================
-- Migration 012: Coding-domain schema
-- ============================================================================
-- Twelve new tables for qualitative coding: code registry, codebook versioning,
-- keyword governance, chunk signals, coding dispatch, reliability, and audit.
-- All tables carry tenant_id + RLS policies. Append-only tables (chunk_signals,
-- chunk_codes, coding_gate_decisions) are enforced by BEFORE UPDATE OR DELETE
-- triggers.
--
-- Sits on master's base (storage/schema.sql + migrations 001-011): chunks and
-- chunks_vectors are the upstream projections this domain reads, and the
-- vector(4096) columns match chunks_vectors.vector exactly. Tenant scoping
-- follows the same app.current_tenant_id GUC + NULLIF policy convention the
-- newer master tables (tags, document_tags, metadata) use; migration 013 then
-- FORCEs RLS on every table created here, mirroring master's 011_force_rls.

SET app.current_tenant_id = '00000000-0000-0000-0000-000000000001';

-- ============================================================================
-- 1. code_registry: Master table for each code (theory-grounded, dual-embedded)
-- ============================================================================

CREATE TABLE IF NOT EXISTS code_registry (
    code_id text NOT NULL,
    tenant_id uuid NOT NULL,
    codebook_version_id uuid NOT NULL,
    name text NOT NULL,
    brief_definition text NOT NULL,
    full_definition text,
    inclusion_criteria text NOT NULL,
    exclusion_criteria text NOT NULL,
    examples jsonb NOT NULL DEFAULT '[]',
    theory jsonb NOT NULL DEFAULT '{}',
    actor_scope text,
    is_provisional boolean NOT NULL DEFAULT false,
    pool_floor numeric,
    residual_floor numeric,
    prototype_vectors jsonb,
    definition_vector vector(4096),
    probe_vector vector(4096),
    superseded_by text,
    created_at timestamptz DEFAULT now(),
    PRIMARY KEY (code_id, codebook_version_id, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_code_registry_tenant ON code_registry(tenant_id);
CREATE INDEX IF NOT EXISTS idx_code_registry_version ON code_registry(codebook_version_id);

ALTER TABLE code_registry ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    CREATE POLICY code_registry_tenant_isolation ON code_registry
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID);
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Policy code_registry_tenant_isolation already exists: %', SQLERRM;
END $$;

-- ============================================================================
-- 2. codebook_versions: Version control and SHA256 hash for codebook changes
-- ============================================================================

CREATE TABLE IF NOT EXISTS codebook_versions (
    version_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    label text NOT NULL,
    sha256 text NOT NULL,
    diff jsonb,
    paradigm text NOT NULL,
    created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_codebook_versions_tenant ON codebook_versions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_codebook_versions_sha256 ON codebook_versions(sha256);

ALTER TABLE codebook_versions ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    CREATE POLICY codebook_versions_tenant_isolation ON codebook_versions
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID);
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Policy codebook_versions_tenant_isolation already exists: %', SQLERRM;
END $$;

-- ============================================================================
-- 3. code_keywords: Inclusion/exclusion keywords with keyness statistics
-- ============================================================================

CREATE TABLE IF NOT EXISTS code_keywords (
    keyword text NOT NULL,
    code_id text NOT NULL,
    tenant_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('inclusion','exclusion')),
    source text NOT NULL,
    ll numeric,
    log_ratio numeric,
    dispersion int,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','retired')),
    created_at timestamptz DEFAULT now(),
    PRIMARY KEY (keyword, code_id, kind, tenant_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_inclusion_keyword ON code_keywords (tenant_id, lower(keyword))
    WHERE kind='inclusion' AND status='active';
CREATE INDEX IF NOT EXISTS idx_code_keywords_code ON code_keywords(code_id);
CREATE INDEX IF NOT EXISTS idx_code_keywords_tenant ON code_keywords(tenant_id);

ALTER TABLE code_keywords ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    CREATE POLICY code_keywords_tenant_isolation ON code_keywords
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID);
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Policy code_keywords_tenant_isolation already exists: %', SQLERRM;
END $$;

-- ============================================================================
-- 4. chunk_signals: Pooling signals (keywords, vectors, similarity scores)
-- Append-only here; migration 015 later makes it upsertable (drops `detail`
-- from the key and this trigger) once pooling.py's recompute path lands.
-- ============================================================================

CREATE TABLE IF NOT EXISTS chunk_signals (
    chunk_id uuid NOT NULL,
    code_id text NOT NULL,
    tenant_id uuid NOT NULL,
    signal text NOT NULL CHECK (signal IN ('keyword','vector','vector_ctx','qa_chain','centroid','residual_sweep')),
    score numeric,
    detail text NOT NULL DEFAULT '',
    created_at timestamptz DEFAULT now(),
    PRIMARY KEY (chunk_id, code_id, signal, detail, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_chunk_signals_chunk ON chunk_signals(chunk_id);
CREATE INDEX IF NOT EXISTS idx_chunk_signals_code ON chunk_signals(code_id);
CREATE INDEX IF NOT EXISTS idx_chunk_signals_tenant ON chunk_signals(tenant_id);

ALTER TABLE chunk_signals ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    CREATE POLICY chunk_signals_tenant_isolation ON chunk_signals
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID);
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Policy chunk_signals_tenant_isolation already exists: %', SQLERRM;
END $$;

-- Append-only trigger for chunk_signals
CREATE OR REPLACE FUNCTION chunk_signals_append_only() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'chunk_signals is append-only: UPDATE and DELETE are not permitted';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS chunk_signals_append_only_trigger ON chunk_signals;
CREATE TRIGGER chunk_signals_append_only_trigger
    BEFORE UPDATE OR DELETE ON chunk_signals
    FOR EACH ROW EXECUTE FUNCTION chunk_signals_append_only();

-- ============================================================================
-- 5. chunk_keyword_hits: Normalized word counts per keyword
-- ============================================================================

CREATE TABLE IF NOT EXISTS chunk_keyword_hits (
    chunk_id uuid NOT NULL,
    keyword text NOT NULL,
    code_id text NOT NULL,
    tenant_id uuid NOT NULL,
    kind text NOT NULL,
    n_hits int NOT NULL,
    PRIMARY KEY (chunk_id, keyword, code_id, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_chunk_keyword_hits_chunk ON chunk_keyword_hits(chunk_id);
CREATE INDEX IF NOT EXISTS idx_chunk_keyword_hits_tenant ON chunk_keyword_hits(tenant_id);

ALTER TABLE chunk_keyword_hits ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    CREATE POLICY chunk_keyword_hits_tenant_isolation ON chunk_keyword_hits
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID);
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Policy chunk_keyword_hits_tenant_isolation already exists: %', SQLERRM;
END $$;

-- ============================================================================
-- 6. answer_ctx_vectors: Question-prepend or context-pooled probe vectors
-- ============================================================================

CREATE TABLE IF NOT EXISTS answer_ctx_vectors (
    chunk_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    vector vector(4096) NOT NULL,
    ctx_source text NOT NULL DEFAULT 'question-prepend',
    built_at timestamptz DEFAULT now(),
    PRIMARY KEY (chunk_id, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_answer_ctx_vectors_tenant ON answer_ctx_vectors(tenant_id);

ALTER TABLE answer_ctx_vectors ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    CREATE POLICY answer_ctx_vectors_tenant_isolation ON answer_ctx_vectors
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID);
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Policy answer_ctx_vectors_tenant_isolation already exists: %', SQLERRM;
END $$;

-- ============================================================================
-- 7. batch_status: Coding batch tracking (pending, in_progress, done, parked, failed)
-- ============================================================================

CREATE TABLE IF NOT EXISTS batch_status (
    batch_id text NOT NULL,
    tenant_id uuid NOT NULL,
    family text,
    n_cells int NOT NULL,
    state text NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending','in_progress','done','parked','failed')),
    attempts int NOT NULL DEFAULT 0,
    manifest jsonb,
    updated_at timestamptz DEFAULT now(),
    PRIMARY KEY (batch_id, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_batch_status_tenant ON batch_status(tenant_id);
CREATE INDEX IF NOT EXISTS idx_batch_status_state ON batch_status(state);

ALTER TABLE batch_status ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    CREATE POLICY batch_status_tenant_isolation ON batch_status
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID);
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Policy batch_status_tenant_isolation already exists: %', SQLERRM;
END $$;

-- ============================================================================
-- 8. chunk_codes: Coding decisions (assign/reject) with rationale + confidence
-- Append-only via trigger.
-- ============================================================================

CREATE TABLE IF NOT EXISTS chunk_codes (
    chunk_id uuid NOT NULL,
    code_id text NOT NULL,
    tenant_id uuid NOT NULL,
    pass int NOT NULL DEFAULT 1,
    batch_id text,
    coder text NOT NULL,
    model text NOT NULL,
    decision text NOT NULL CHECK (decision IN ('assign','reject')),
    confidence numeric NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    tier_signals jsonb NOT NULL DEFAULT '{}',
    rationale text,
    rationale_vector vector(4096),
    evidence_quote text,
    qa_context_used boolean NOT NULL DEFAULT false,
    route text,
    created_at timestamptz DEFAULT now(),
    CHECK (decision <> 'assign' OR evidence_quote IS NOT NULL),
    PRIMARY KEY (chunk_id, code_id, coder, pass, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_chunk_codes_chunk ON chunk_codes(chunk_id);
CREATE INDEX IF NOT EXISTS idx_chunk_codes_code ON chunk_codes(code_id);
CREATE INDEX IF NOT EXISTS idx_chunk_codes_tenant ON chunk_codes(tenant_id);
CREATE INDEX IF NOT EXISTS idx_chunk_codes_batch ON chunk_codes(batch_id);
CREATE INDEX IF NOT EXISTS idx_chunk_codes_coder ON chunk_codes(coder);

ALTER TABLE chunk_codes ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    CREATE POLICY chunk_codes_tenant_isolation ON chunk_codes
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID);
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Policy chunk_codes_tenant_isolation already exists: %', SQLERRM;
END $$;

-- Append-only trigger for chunk_codes
CREATE OR REPLACE FUNCTION chunk_codes_append_only() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'chunk_codes is append-only: UPDATE and DELETE are not permitted';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS chunk_codes_append_only_trigger ON chunk_codes;
CREATE TRIGGER chunk_codes_append_only_trigger
    BEFORE UPDATE OR DELETE ON chunk_codes
    FOR EACH ROW EXECUTE FUNCTION chunk_codes_append_only();

-- ============================================================================
-- 9. final_codes: Routing outcomes (accepted, rejected, manual_review)
-- ============================================================================

CREATE TABLE IF NOT EXISTS final_codes (
    chunk_id uuid NOT NULL,
    code_id text NOT NULL,
    tenant_id uuid NOT NULL,
    status text NOT NULL CHECK (status IN ('accepted','rejected','manual_review')),
    route text NOT NULL,
    confidence numeric NOT NULL,
    decided_at timestamptz DEFAULT now(),
    PRIMARY KEY (chunk_id, code_id, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_final_codes_chunk ON final_codes(chunk_id);
CREATE INDEX IF NOT EXISTS idx_final_codes_code ON final_codes(code_id);
CREATE INDEX IF NOT EXISTS idx_final_codes_tenant ON final_codes(tenant_id);

ALTER TABLE final_codes ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    CREATE POLICY final_codes_tenant_isolation ON final_codes
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID);
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Policy final_codes_tenant_isolation already exists: %', SQLERRM;
END $$;

-- ============================================================================
-- 10. chunk_status: Per-chunk coding state (disposition, speaker role)
-- ============================================================================

CREATE TABLE IF NOT EXISTS chunk_status (
    chunk_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    doc_id uuid NOT NULL,
    speaker_role text,
    in_any_pool boolean NOT NULL DEFAULT false,
    n_codes_final int NOT NULL DEFAULT 0,
    disposition text NOT NULL DEFAULT 'pending'
        CHECK (disposition IN ('pending','coded','no_code_applies','manual_review')),
    PRIMARY KEY (chunk_id, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_chunk_status_tenant ON chunk_status(tenant_id);
CREATE INDEX IF NOT EXISTS idx_chunk_status_disposition ON chunk_status(disposition);

ALTER TABLE chunk_status ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    CREATE POLICY chunk_status_tenant_isolation ON chunk_status
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID);
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Policy chunk_status_tenant_isolation already exists: %', SQLERRM;
END $$;

-- ============================================================================
-- 11. doc_extraction_audit: Exhaustiveness tracking (sweep count, miss rate)
-- ============================================================================

CREATE TABLE IF NOT EXISTS doc_extraction_audit (
    doc_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    sweep_n int NOT NULL,
    miss_rate_point numeric,
    miss_rate_ub95 numeric,
    exhausted boolean NOT NULL DEFAULT false,
    audited_at timestamptz DEFAULT now(),
    PRIMARY KEY (doc_id, sweep_n, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_doc_extraction_audit_tenant ON doc_extraction_audit(tenant_id);
CREATE INDEX IF NOT EXISTS idx_doc_extraction_audit_doc ON doc_extraction_audit(doc_id);

ALTER TABLE doc_extraction_audit ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    CREATE POLICY doc_extraction_audit_tenant_isolation ON doc_extraction_audit
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID);
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Policy doc_extraction_audit_tenant_isolation already exists: %', SQLERRM;
END $$;

-- ============================================================================
-- 12. coding_gate_decisions: Durable human-gate audit trail
-- Append-only via trigger.
-- ============================================================================

CREATE TABLE IF NOT EXISTS coding_gate_decisions (
    gate_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    phase text NOT NULL,
    subject text NOT NULL,
    verdict jsonb NOT NULL,
    approved_by text NOT NULL,
    human_reply_verbatim text NOT NULL,
    at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_coding_gate_decisions_tenant ON coding_gate_decisions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_coding_gate_decisions_phase ON coding_gate_decisions(phase);

ALTER TABLE coding_gate_decisions ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    CREATE POLICY coding_gate_decisions_tenant_isolation ON coding_gate_decisions
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID);
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Policy coding_gate_decisions_tenant_isolation already exists: %', SQLERRM;
END $$;

-- Append-only trigger for coding_gate_decisions
CREATE OR REPLACE FUNCTION coding_gate_decisions_append_only() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'coding_gate_decisions is append-only: UPDATE and DELETE are not permitted';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS coding_gate_decisions_append_only_trigger ON coding_gate_decisions;
CREATE TRIGGER coding_gate_decisions_append_only_trigger
    BEFORE UPDATE OR DELETE ON coding_gate_decisions
    FOR EACH ROW EXECUTE FUNCTION coding_gate_decisions_append_only();
