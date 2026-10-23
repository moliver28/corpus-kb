-- DuckDB twin of pg migration 032 (statement conflicts, Living Corpus W3).
-- Translation per the house convention (see 028 header): UUID -> VARCHAR,
-- jsonb -> JSON. No RLS: the DuckDB gateway enforces the tenant predicate
-- per statement.

CREATE TABLE IF NOT EXISTS statement_conflicts (
    conflict_id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR NOT NULL,
    statement_a_id VARCHAR NOT NULL REFERENCES statements(statement_id),
    statement_b_id VARCHAR NOT NULL REFERENCES statements(statement_id),
    anchor_name TEXT NOT NULL,
    kind TEXT NOT NULL,
    gate_reached TEXT NOT NULL,
    verdict TEXT NOT NULL,
    nli_verdict TEXT,
    resolved_verdict TEXT,
    resolved_by TEXT,
    metadata JSON NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, statement_a_id, statement_b_id, kind)
);

CREATE INDEX IF NOT EXISTS idx_statement_conflicts_tenant_verdict
    ON statement_conflicts(tenant_id, resolved_verdict NULLS FIRST, verdict, created_at);
