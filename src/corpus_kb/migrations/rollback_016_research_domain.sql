-- ============================================================================
-- Rollback for migration 016 (research domain read models)
-- ============================================================================
-- Drops the NEW research tables and clears the research_projection checkpoint
-- rows so a re-apply does not resume past dropped tables. Also drops the
-- research columns added to documents and the last_sequence checkpoint column.
--
-- NOT touched: event_store/snapshot_store (lib-owned), the legacy chunk-keyed
-- coding tables (012-015), and the ingest-owned operational state
-- (research_transcript_text, ingested_files, embedding_cache) — a read model
-- cannot be rebuilt from a reference, so the content-addressed turn store,
-- the file ledger, and the write-once embedding cache survive the rollback.
-- ============================================================================

DROP TABLE IF EXISTS research_reviews;
DROP TABLE IF EXISTS research_signals;
DROP TABLE IF EXISTS research_assignments;
DROP TABLE IF EXISTS research_exchanges;
DROP TABLE IF EXISTS research_units;
DROP TABLE IF EXISTS research_speakers;
DROP TABLE IF EXISTS research_runs;
DROP TABLE IF EXISTS research_projects;

DROP INDEX IF EXISTS idx_documents_project;
DROP INDEX IF EXISTS idx_documents_source_hash;
ALTER TABLE documents DROP COLUMN IF EXISTS project_id;
ALTER TABLE documents DROP COLUMN IF EXISTS title;
ALTER TABLE documents DROP COLUMN IF EXISTS source_path;
ALTER TABLE documents DROP COLUMN IF EXISTS source_hash;
ALTER TABLE documents DROP COLUMN IF EXISTS parser_name;
ALTER TABLE documents DROP COLUMN IF EXISTS parser_version;
ALTER TABLE documents DROP COLUMN IF EXISTS doc_version;
ALTER TABLE documents DROP COLUMN IF EXISTS parse_quality;

-- Clear projection positions that reference the dropped read models so the
-- re-applied projection starts from sequence 0 (replays cleanly).
DELETE FROM projection_checkpoints WHERE projection_name = 'ResearchProjection';

ALTER TABLE projection_checkpoints DROP COLUMN IF EXISTS last_sequence;
