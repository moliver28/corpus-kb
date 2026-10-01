-- ============================================================================
-- Migration 007: chunk dedup + provenance
-- ============================================================================
-- Additive columns supporting content-hash dedup, supersede-on-change, and
-- tombstone-on-removal for the direct-write ingest path (src/tools/
-- ingest_common.py). See docs/superpowers/plans or ~/.claude/plans for the
-- feature writeup. Never drops/rewrites existing rows.

SET app.current_tenant_id = '00000000-0000-0000-0000-000000000001';

ALTER TABLE chunks
  ADD COLUMN IF NOT EXISTS chunk_hash VARCHAR(64),
  ADD COLUMN IF NOT EXISTS source_timestamp TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS superseded_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS tombstoned_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_chunks_active
  ON chunks(tenant_id, doc_id)
  WHERE tombstoned_at IS NULL AND superseded_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_chunks_hash ON chunks(chunk_hash);

ALTER TABLE chunks_vectors ADD COLUMN IF NOT EXISTS dimensions INT;

UPDATE chunks_vectors SET dimensions = 4096 WHERE dimensions IS NULL;
