-- ============================================================================
-- Migration 007: chunk dedup + provenance columns (DuckDB mirror)
-- ============================================================================
-- Pair of migrations/007_chunk_dedup_provenance.sql. The four ALTER TABLE
-- columns arrive one statement per column (DuckDB allows one ALTER per
-- statement). idx_chunks_active is partial on Postgres (WHERE tombstoned_at
-- IS NULL AND superseded_at IS NULL); DuckDB gets the same columns without
-- the predicate. The SET of the tenant GUC is dropped — the gateway carries
-- tenant predicates.
-- ============================================================================

ALTER TABLE chunks ADD COLUMN IF NOT EXISTS chunk_hash VARCHAR(64);
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS source_timestamp TIMESTAMPTZ;
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS superseded_at TIMESTAMPTZ;
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS tombstoned_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_chunks_active ON chunks(tenant_id, doc_id);
CREATE INDEX IF NOT EXISTS idx_chunks_hash ON chunks(chunk_hash);

ALTER TABLE chunks_vectors ADD COLUMN IF NOT EXISTS dimensions INT;

UPDATE chunks_vectors SET dimensions = 4096 WHERE dimensions IS NULL;
