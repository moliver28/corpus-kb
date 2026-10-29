-- ============================================================================
-- Migration 037: the ingest queue (textbook-ingestion program, Wave 1)
-- ============================================================================
-- ingest_queue is the durable work-order table the enqueue/worker/UI waves
-- share. One row = one file to ingest, enqueued idempotently by
-- content_hash (sha256 of the absolute path + profile + canonical
-- strategy config). The unique PARTIAL index keeps that idempotency
-- window honest: only one ACTIVE (pending/running) row may exist per
-- (tenant_id, content_hash); a finished row never blocks a re-enqueue.
--
-- The project linkage copies migration 036's precedent exactly: a PLAIN
-- nullable column, no FK (documents.project_id, 016, set the same
-- precedent; the research_projects read model is projection-owned).
-- run_id is the same plain nullable shape; a worker records the
-- ingest_runs ledger row it opened when it claims the job.
--
-- RLS mirrors the 022 convention: ENABLE + DROP POLICY IF EXISTS + tenant
-- GUC policy + FORCE.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS +
-- DROP POLICY IF EXISTS. Parameterized DDL only — no dynamic DDL beyond
-- the 022 policy block.
-- ============================================================================

CREATE TABLE IF NOT EXISTS ingest_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    path TEXT NOT NULL,
    title TEXT,
    project_id UUID,
    profile TEXT,
    strategy_config JSONB NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','running','done','failed','cancelled')),
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    last_error TEXT,
    run_id UUID,
    content_hash TEXT NOT NULL,
    claimed_by TEXT,
    heartbeat_at TIMESTAMPTZ,
    queued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ
);

-- Idempotent enqueue backstop: one ACTIVE row per (tenant_id, content_hash).
-- The dedupe probe reads the same window; the index closes the race.
CREATE UNIQUE INDEX IF NOT EXISTS idx_ingest_queue_active_hash
    ON ingest_queue(tenant_id, content_hash)
    WHERE status IN ('pending', 'running');

-- Worker claim scan: oldest pending/running first.
CREATE INDEX IF NOT EXISTS idx_ingest_queue_claim
    ON ingest_queue(status, queued_at);

-- Project filter over the queue's rows (the 036 index pattern).
CREATE INDEX IF NOT EXISTS idx_ingest_queue_tenant_project
    ON ingest_queue(tenant_id, project_id);

-- ----------------------------------------------------------------------------
-- RLS: tenant isolation on the queue (migration 016/022 policy pattern,
-- ENABLE + FORCE, DROP POLICY IF EXISTS for re-apply)
-- ----------------------------------------------------------------------------

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['ingest_queue'] LOOP
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
