-- ============================================================================
-- Migration 051: analysis_steps ledger (TAO T0, TD-13, R11) — DuckDB mirror
-- ============================================================================
-- Pair of migrations/050_analysis_steps.sql. Translation rules (duckdb 000
-- header): UUID -> VARCHAR; jsonb -> JSON. RLS policies and the DO policy
-- block are dropped; the gateway enforces tenant scoping. Additive only:
-- no rollback mirror.

CREATE TABLE IF NOT EXISTS analysis_steps (
    step_id VARCHAR PRIMARY KEY,
    tenant_id VARCHAR NOT NULL,
    run_id VARCHAR,
    parent_step_id VARCHAR,
    seq int NOT NULL DEFAULT 0,
    kind TEXT NOT NULL CHECK (kind IN ('planned', 'ad_hoc')),
    operation TEXT NOT NULL,
    operation_version TEXT,
    inputs_table TEXT,
    inputs_column TEXT,
    inputs_filter TEXT,
    inputs_digest TEXT,
    parameters JSON NOT NULL DEFAULT '{}',
    prompt_id TEXT,
    prompt_version TEXT,
    prompt_digest TEXT,
    model_name TEXT,
    model_digest TEXT,
    step_key TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'completed', 'failed')),
    outputs_table TEXT,
    outputs_rows int,
    actor TEXT,
    used JSON NOT NULL DEFAULT '[]',
    was_generated_by TEXT,
    was_derived_from TEXT
);

CREATE INDEX IF NOT EXISTS idx_analysis_steps_run ON analysis_steps (tenant_id, run_id);
CREATE INDEX IF NOT EXISTS idx_analysis_steps_key ON analysis_steps (tenant_id, step_key);
CREATE INDEX IF NOT EXISTS idx_analysis_steps_started ON analysis_steps (tenant_id, started_at);
