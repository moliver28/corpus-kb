-- ============================================================================
-- Migration 048: master membership (W4, spec s3)
-- ============================================================================
-- Membership is data: project_claims holds BOTH origin membership (one row
-- per documents.project_id, backfilled below) and cross-project claims.
-- project_activity stamps the ingest/query/analysis touches the active-state
-- rule reads; master_state caches the resolved active project per tenant.
-- Additive only: no destructive step, so no rollback mirror (P2 plan W4-2).

CREATE TABLE IF NOT EXISTS project_claims (
    tenant_id UUID NOT NULL,
    project_id UUID NOT NULL,
    doc_id UUID NOT NULL,
    source_project_id UUID,
    claimed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, project_id, doc_id),
    FOREIGN KEY (doc_id) REFERENCES documents(doc_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS project_activity (
    tenant_id UUID NOT NULL,
    project_id UUID NOT NULL,
    kind TEXT NOT NULL,
    last_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, project_id, kind)
);

CREATE TABLE IF NOT EXISTS master_state (
    tenant_id UUID NOT NULL PRIMARY KEY,
    active_project_id UUID,
    since_ts TIMESTAMPTZ,
    updated_ts TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Backfill origin membership: one claim per documents row with a project,
-- source_project_id = project_id (the origin marks itself as the source).
INSERT INTO project_claims (tenant_id, project_id, doc_id, source_project_id)
SELECT tenant_id, project_id, doc_id, project_id FROM documents WHERE project_id IS NOT NULL
ON CONFLICT (tenant_id, project_id, doc_id) DO NOTHING;
