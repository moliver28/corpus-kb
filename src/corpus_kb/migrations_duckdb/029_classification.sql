-- ============================================================================
-- Migration 029: few-shot classification layer (DuckDB mirror, 2 tables)
-- ============================================================================
-- Pair of migrations/029_classification.sql. Translation: UUID -> VARCHAR,
-- jsonb -> JSON, DOUBLE PRECISION -> DOUBLE. FK ON DELETE CASCADE actions
-- are Postgres constraint clauses; the gateway enforces the delete
-- behavior. The RLS block (ENABLE + FORCE + tenant GUC policies) is
-- dropped: DuckDB has no row-level security, and the gateway carries an
-- explicit tenant_id predicate on every statement (program doc 1.1/3.1).
-- ============================================================================

-- 1. label_sets: the seed label sets (input, kept)
CREATE TABLE IF NOT EXISTS label_sets (
    set_id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR NOT NULL,
    name TEXT NOT NULL,
    classes JSON NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2. chunk_predictions: the per-chunk prediction (derived, replaceable)
CREATE TABLE IF NOT EXISTS chunk_predictions (
    tenant_id VARCHAR NOT NULL,
    chunk_id VARCHAR NOT NULL REFERENCES chunks(chunk_id),
    -- Postgres action: ON DELETE CASCADE.
    set_id VARCHAR NOT NULL REFERENCES label_sets(set_id),
    -- Postgres action: ON DELETE CASCADE.
    label TEXT NOT NULL,
    probability DOUBLE NOT NULL DEFAULT 0.0
        CHECK (probability >= 0.0 AND probability <= 1.0),
    calibrated_probability DOUBLE
        CHECK (calibrated_probability IS NULL
               OR (calibrated_probability >= 0.0
                   AND calibrated_probability <= 1.0)),
    tier TEXT NOT NULL,
    review BOOLEAN NOT NULL DEFAULT FALSE,
    params JSON NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, chunk_id, set_id)
);

CREATE INDEX IF NOT EXISTS idx_label_sets_tenant
    ON label_sets(tenant_id);
CREATE INDEX IF NOT EXISTS idx_chunk_predictions_set
    ON chunk_predictions(tenant_id, set_id);
