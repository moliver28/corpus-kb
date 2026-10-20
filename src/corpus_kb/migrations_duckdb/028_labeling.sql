-- ============================================================================
-- Migration 028: weak-supervision labeling layer (DuckDB mirror, 2 tables)
-- ============================================================================
-- Pair of migrations/028_labeling.sql. Translation: UUID -> VARCHAR,
-- jsonb -> JSON, DOUBLE PRECISION -> DOUBLE. FK ON DELETE CASCADE actions
-- are Postgres constraint clauses; the gateway enforces the delete
-- behavior. The RLS block (ENABLE + FORCE + tenant GUC policies) is
-- dropped: DuckDB has no row-level security, and the gateway carries an
-- explicit tenant_id predicate on every statement (program doc 1.1/3.1).
-- ============================================================================

-- 1. label_functions: the authored labeling functions (input, kept)
CREATE TABLE IF NOT EXISTS label_functions (
    lf_id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    config JSON NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2. chunk_labels: the aggregated per-chunk label (derived, replaceable)
CREATE TABLE IF NOT EXISTS chunk_labels (
    tenant_id VARCHAR NOT NULL,
    chunk_id VARCHAR NOT NULL REFERENCES chunks(chunk_id),
    -- Postgres action: ON DELETE CASCADE.
    label TEXT NOT NULL,
    probability DOUBLE NOT NULL DEFAULT 0.0
        CHECK (probability >= 0.0 AND probability <= 1.0),
    detail JSON NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_label_functions_tenant
    ON label_functions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_chunk_labels_tenant_label
    ON chunk_labels(tenant_id, label);
