-- ============================================================================
-- Migration 050: analysis_steps ledger (TAO T0, TD-13, R11)
-- ============================================================================
-- One step ledger for planned and ad-hoc analysis steps. The rows store
-- references and digests, never corpus text (TD-13 point 3). PROV field
-- names per AR-7: `used` (entity references), `was_generated_by`,
-- `was_derived_from`. Additive only: no destructive step, so no rollback
-- mirror.

CREATE TABLE IF NOT EXISTS analysis_steps (
    step_id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL,
    run_id uuid,
    parent_step_id uuid,
    seq int NOT NULL DEFAULT 0,
    kind text NOT NULL CHECK (kind IN ('planned', 'ad_hoc')),
    operation text NOT NULL,
    operation_version text,
    inputs_table text,
    inputs_column text,
    inputs_filter text,
    inputs_digest text,
    parameters jsonb NOT NULL DEFAULT '{}',
    prompt_id text,
    prompt_version text,
    prompt_digest text,
    model_name text,
    model_digest text,
    step_key text,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'completed', 'failed')),
    outputs_table text,
    outputs_rows int,
    actor text,
    used jsonb NOT NULL DEFAULT '[]',
    was_generated_by text,
    was_derived_from text
);

CREATE INDEX IF NOT EXISTS idx_analysis_steps_run ON analysis_steps (tenant_id, run_id);
CREATE INDEX IF NOT EXISTS idx_analysis_steps_key ON analysis_steps (tenant_id, step_key);
CREATE INDEX IF NOT EXISTS idx_analysis_steps_started ON analysis_steps (tenant_id, started_at);

ALTER TABLE analysis_steps ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    CREATE POLICY analysis_steps_tenant_isolation ON analysis_steps
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID);
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Policy analysis_steps_tenant_isolation already exists: %', SQLERRM;
END $$;
