-- ============================================================================
-- Migration 049: master membership (W4, spec s3) — DuckDB mirror of pg 048
-- ============================================================================
-- Translation rules (duckdb 000 header): UUID -> VARCHAR; TIMESTAMPTZ stays.
-- The project_claims FK is PLAIN (DuckDB cannot declare ON DELETE actions;
-- declared drift row fk/project_claims/doc_id — storage.duckdb_cascade owns
-- the delete action). Additive only: no rollback mirror (P2 plan W4-2).

CREATE TABLE IF NOT EXISTS project_claims (
    tenant_id VARCHAR NOT NULL,
    project_id VARCHAR NOT NULL,
    doc_id VARCHAR NOT NULL,
    source_project_id VARCHAR,
    claimed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, project_id, doc_id),
    FOREIGN KEY (doc_id) REFERENCES documents(doc_id)
);

CREATE TABLE IF NOT EXISTS project_activity (
    tenant_id VARCHAR NOT NULL,
    project_id VARCHAR NOT NULL,
    kind VARCHAR NOT NULL,
    last_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, project_id, kind)
);

CREATE TABLE IF NOT EXISTS master_state (
    tenant_id VARCHAR NOT NULL PRIMARY KEY,
    active_project_id VARCHAR,
    since_ts TIMESTAMPTZ,
    updated_ts TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Backfill origin membership: one claim per documents row with a project,
-- source_project_id = project_id (the origin marks itself as the source).
INSERT INTO project_claims (tenant_id, project_id, doc_id, source_project_id)
SELECT tenant_id, project_id, doc_id, project_id FROM documents WHERE project_id IS NOT NULL
ON CONFLICT (tenant_id, project_id, doc_id) DO NOTHING;
