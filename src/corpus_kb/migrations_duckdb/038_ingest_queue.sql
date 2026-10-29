-- ============================================================================
-- Migration 038: the ingest queue (DuckDB mirror)
-- ============================================================================
-- Pair of migrations/037_ingest_queue.sql. Translation rules (000 header):
--   UUID -> VARCHAR (values stay canonical uuid text), JSONB -> JSON.
--   RLS and policies are dropped; the gateway carries tenant predicates.
--   The unique partial index loses its WHERE predicate (DuckDB has no
--   partial indexes; the 022 mirror pattern) and stays UNIQUE — the
--   one-active-row-per-hash rule the enqueue probe relies on.
-- DuckDB is at 038 because 030 is its own ledger migration; see that
-- file for the numbering note.
-- Idempotent: CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS.
-- ============================================================================

CREATE TABLE IF NOT EXISTS ingest_queue (
    id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR NOT NULL,
    path TEXT NOT NULL,
    title TEXT,
    project_id VARCHAR,
    profile TEXT,
    strategy_config JSON NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','running','done','failed','cancelled')),
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    last_error TEXT,
    run_id VARCHAR,
    content_hash TEXT NOT NULL,
    claimed_by TEXT,
    heartbeat_at TIMESTAMPTZ,
    queued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ
);

-- Postgres keeps this index partial (WHERE status IN ('pending','running')).
CREATE UNIQUE INDEX IF NOT EXISTS idx_ingest_queue_active_hash
    ON ingest_queue(tenant_id, content_hash);

CREATE INDEX IF NOT EXISTS idx_ingest_queue_claim
    ON ingest_queue(status, queued_at);

CREATE INDEX IF NOT EXISTS idx_ingest_queue_tenant_project
    ON ingest_queue(tenant_id, project_id);
