-- ============================================================================
-- Migration 008: typed relations provenance (DuckDB mirror)
-- ============================================================================
-- Pair of migrations/008_typed_relations.sql. Four provenance columns on
-- relations, one per statement. The chunk_id FK column is missing here on
-- purpose: DuckDB cannot add a foreign-key column by ALTER, so 000 creates
-- relations.chunk_id with its REFERENCES clause (Postgres action: ON DELETE
-- SET NULL; the gateway enforces it). The widened unique key
-- relations_triple_chunk_uniq becomes a unique index (DuckDB cannot add a
-- table constraint after CREATE TABLE). The Postgres migration drops the
-- narrower schema.sql key first; this mirror never created that narrow key
-- (see 000), so nothing to drop.
-- ============================================================================

ALTER TABLE relations ADD COLUMN IF NOT EXISTS confidence DOUBLE;
ALTER TABLE relations ADD COLUMN IF NOT EXISTS extractor_id VARCHAR(100);
ALTER TABLE relations ADD COLUMN IF NOT EXISTS model_version VARCHAR(100);
ALTER TABLE relations ADD COLUMN IF NOT EXISTS prompt_version VARCHAR(50);

CREATE INDEX IF NOT EXISTS idx_relations_chunk ON relations(chunk_id);

-- UNIQUE (tenant_id, source_entity_id, target_entity_id, relation_type,
-- chunk_id): NULL chunk_id rows stay distinct, as on Postgres.
CREATE UNIQUE INDEX IF NOT EXISTS relations_triple_chunk_uniq
    ON relations (tenant_id, source_entity_id, target_entity_id, relation_type, chunk_id);
