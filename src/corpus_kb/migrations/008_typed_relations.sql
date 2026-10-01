-- ============================================================================
-- Migration 008: typed relations provenance
-- ============================================================================
-- Adds per-relation provenance (which chunk, confidence, extractor/model/
-- prompt version) so LLM-extracted typed triples (replacing co-occurrence
-- edges) are traceable and reproducible. The unique key is widened to
-- include chunk_id: the SAME triple asserted in two different chunks now
-- keeps both provenance rows instead of collapsing to first-writer-wins,
-- which Feature 6 (citations) depends on for per-source traceability.

SET app.current_tenant_id = '00000000-0000-0000-0000-000000000001';

ALTER TABLE relations
  ADD COLUMN IF NOT EXISTS chunk_id UUID REFERENCES chunks(chunk_id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS confidence FLOAT,
  ADD COLUMN IF NOT EXISTS extractor_id VARCHAR(100),
  ADD COLUMN IF NOT EXISTS model_version VARCHAR(100),
  ADD COLUMN IF NOT EXISTS prompt_version VARCHAR(50);

CREATE INDEX IF NOT EXISTS idx_relations_chunk ON relations(chunk_id);

DO $$
BEGIN
  ALTER TABLE relations DROP CONSTRAINT IF EXISTS relations_tenant_id_source_entity_id_target_entity_id_relation_type_key;
  ALTER TABLE relations DROP CONSTRAINT IF EXISTS relations_tenant_id_source_entity_id_target_entity_id_relat_key;
  ALTER TABLE relations ADD CONSTRAINT relations_triple_chunk_uniq
    UNIQUE (tenant_id, source_entity_id, target_entity_id, relation_type, chunk_id);
EXCEPTION WHEN OTHERS THEN
  RAISE WARNING 'relations_triple_chunk_uniq already applied or conflicting: %', SQLERRM;
END $$;
