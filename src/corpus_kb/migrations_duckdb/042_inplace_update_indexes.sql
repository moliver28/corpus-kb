-- ============================================================================
-- Migration 042 (DuckDB): drop the 7 plain lookup indexes (issue #330)
-- ============================================================================
-- Pair of migrations/041_inplace_update_indexes.sql. DuckDB 1.5.x runs an
-- UPDATE of an indexed column as a delete plus an insert, which fails with
-- "key is still referenced" while another table references the row. These
-- seven indexes sit on exactly such columns (status/state/counter and
-- lookup columns of referenced rows). Performance note: the lookups scan
-- the table, which is fast at study scale. Measure before adding one back.
-- ============================================================================

DROP INDEX IF EXISTS idx_research_assignments_status;
DROP INDEX IF EXISTS idx_codebook_releases_state;
DROP INDEX IF EXISTS idx_documents_tenant_date;
DROP INDEX IF EXISTS idx_documents_hash;
DROP INDEX IF EXISTS idx_documents_project;
DROP INDEX IF EXISTS idx_documents_source_hash;
DROP INDEX IF EXISTS idx_chunks_hash;
