-- ============================================================================
-- Rollback migration 016 (DuckDB): drop the research domain read models.
-- ============================================================================
-- Pair of migrations/rollback_016_research_domain.sql: the same ordered
-- drops. Child tables first, then the documents columns the migration
-- added, then the projection checkpoint state. SET app.current_tenant_id
-- is a Postgres session GUC and is dropped here.
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

DELETE FROM projection_checkpoints WHERE projection_name = 'ResearchProjection';

ALTER TABLE projection_checkpoints DROP COLUMN IF EXISTS last_sequence;
