-- ============================================================================
-- Migration 019: codebook release read models (DuckDB mirror, 4 tables)
-- ============================================================================
-- Pair of migrations/019_codebook_releases.sql. Translation: UUID ->
-- VARCHAR, jsonb -> JSON, BIGINT IDENTITY -> sequence default.
--   * uq_codebook_releases_released is partial-unique on Postgres
--     (WHERE state = 'released'). DuckDB has no partial indexes, and a full
--     unique index would forbid legal draft rows. It becomes a plain index;
--     the gateway enforces the one-released-row rule.
--   * btrim() does not exist in DuckDB; the CHECKs use trim().
--   * The three immutability triggers are dropped (bodies as comments);
--     the gateway enforces release immutability and the tenant-scoped
--     child-parent check.
-- ============================================================================

-- 1. codebook_releases
CREATE TABLE IF NOT EXISTS codebook_releases (
    release_id VARCHAR PRIMARY KEY,
    tenant_id VARCHAR NOT NULL,
    project_id VARCHAR,
    codebook_id VARCHAR NOT NULL,
    codebook_version VARCHAR NOT NULL,
    codebook_version_sha256 CHAR(64) NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'draft_candidate'
        CHECK (state IN ('draft_candidate','released','superseded','retired')),
    profile TEXT NOT NULL DEFAULT 'team-codebook',
    parent_release_id VARCHAR,
    manifest_json JSON NOT NULL DEFAULT '{}',
    manifest_sha256 CHAR(64) NOT NULL,
    superseded_by_release_id VARCHAR,
    superseded_at TIMESTAMPTZ,
    retired_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_at TIMESTAMPTZ,
    creator TEXT NOT NULL DEFAULT '',
    approver TEXT
);

-- Lookup index for the one-released-row rule (gateway-enforced).
CREATE INDEX IF NOT EXISTS uq_codebook_releases_released
    ON codebook_releases (tenant_id, codebook_id, codebook_version);

CREATE INDEX IF NOT EXISTS idx_codebook_releases_state
    ON codebook_releases (tenant_id, state);
CREATE INDEX IF NOT EXISTS idx_codebook_releases_codebook
    ON codebook_releases (tenant_id, codebook_id);

-- 2. release_gate_results
CREATE TABLE IF NOT EXISTS release_gate_results (
    tenant_id VARCHAR NOT NULL,
    release_id VARCHAR NOT NULL REFERENCES codebook_releases(release_id),
    -- Postgres action: ON DELETE CASCADE.
    gate_id TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('pass','fail','not_evaluable','waived')),
    value JSON,
    threshold JSON,
    reason TEXT,
    evidence_refs JSON NOT NULL DEFAULT '[]',
    evaluator JSON NOT NULL DEFAULT '{}',
    evaluated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, release_id, gate_id)
);

-- 3. release_waivers
CREATE TABLE IF NOT EXISTS release_waivers (
    tenant_id VARCHAR NOT NULL,
    release_id VARCHAR NOT NULL REFERENCES codebook_releases(release_id),
    -- Postgres action: ON DELETE CASCADE.
    gate_id TEXT NOT NULL,
    justification TEXT NOT NULL CHECK (trim(justification) <> ''),
    approver TEXT NOT NULL CHECK (trim(approver) <> ''),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, release_id, gate_id)
);

-- 4. audit_partitions
CREATE SEQUENCE IF NOT EXISTS audit_partitions_audit_id_seq START 1;

CREATE TABLE IF NOT EXISTS audit_partitions (
    audit_id BIGINT PRIMARY KEY DEFAULT nextval('audit_partitions_audit_id_seq'),
    tenant_id VARCHAR NOT NULL,
    project_id VARCHAR,
    scope TEXT NOT NULL,
    unit_id BIGINT NOT NULL,
    partition TEXT NOT NULL
        CHECK (partition IN ('discovery','calibration','test','audit')),
    stratum TEXT,
    inclusion_probability REAL,
    sampled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    seed BIGINT,
    UNIQUE (tenant_id, scope, unit_id)
);

CREATE INDEX IF NOT EXISTS idx_audit_partitions_scope
    ON audit_partitions (tenant_id, scope, partition);
CREATE INDEX IF NOT EXISTS idx_audit_partitions_unit
    ON audit_partitions (tenant_id, unit_id);

-- 5. Dropped immutability triggers (gateway enforcement, program doc 8):
-- trg_codebook_releases_immutability (BEFORE UPDATE OR DELETE): blocks the
--   DELETE of a released row; on UPDATE of a released row allows only the
--   supersede/retire state transitions (state + bookkeeping columns, never
--   manifest, version identity, or approval).
-- trg_release_gate_results_frozen and trg_release_waivers_frozen (BEFORE
--   INSERT OR UPDATE OR DELETE): reject children whose (tenant_id,
--   release_id) does not resolve to a visible release row, and freeze all
--   child writes once the parent release is released.
