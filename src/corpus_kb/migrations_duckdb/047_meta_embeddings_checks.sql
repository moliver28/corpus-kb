-- ============================================================================
-- Migration 047 (DuckDB): meta_embeddings gains pg 034's two CHECKs (#527 G04)
-- ============================================================================
-- Postgres migrations/034_meta_embeddings.sql declares
-- CHECK (meta_kind IN ('topic', 'belief_summary', 'document', 'profile'))
-- and CHECK (source_count >= 0). The DuckDB mirror 035 omitted both, so the
-- mirror admitted rows Postgres rejects.
--
-- DuckDB 1.5.x has no ALTER TABLE ADD CHECK (verified on 1.5.6: the ALTER
-- raises NotImplementedException), so the CHECKs land through a table
-- rebuild in the 041 pattern. The runner (run_duckdb_migrations) wraps this
-- file in ONE transaction: a failed rebuild rolls back and the old table
-- with its rows stays. Rows copy out to the temp table BEFORE the drop, so
-- every row is preserved exactly.
--
-- Additive/constraint-only: no rollback mirror (the rule requires mirrors
-- for destructive steps only; rollback = revert this migration).
-- ============================================================================

CREATE TEMP TABLE tmp_meta_embeddings AS
    SELECT meta_id, tenant_id, meta_kind, meta_key, label, embedding,
           source_count, built_at
    FROM meta_embeddings;

DROP TABLE meta_embeddings;

CREATE TABLE meta_embeddings (
    meta_id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR NOT NULL,
    -- pg 034: the four aggregate kinds are a closed vocabulary.
    meta_kind TEXT NOT NULL
        CHECK (meta_kind IN ('topic', 'belief_summary', 'document', 'profile')),
    meta_key TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    embedding FLOAT[2560] NOT NULL,
    -- pg 034: a negative source count is never written.
    source_count INT NOT NULL DEFAULT 0 CHECK (source_count >= 0),
    built_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, meta_kind, meta_key)
);

INSERT INTO meta_embeddings (meta_id, tenant_id, meta_kind, meta_key, label,
                             embedding, source_count, built_at)
    SELECT meta_id, tenant_id, meta_kind, meta_key, label, embedding,
           source_count, built_at
    FROM tmp_meta_embeddings;

DROP TABLE tmp_meta_embeddings;

-- Index from 035, verbatim (DROP TABLE removed it with the old table).
CREATE INDEX IF NOT EXISTS idx_meta_embeddings_kind
    ON meta_embeddings(tenant_id, meta_kind);
