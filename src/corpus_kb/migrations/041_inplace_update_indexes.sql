-- ============================================================================
-- Migration 041: drop the 7 plain indexes on in-place-updated columns
-- ============================================================================
-- DuckDB 1.5.x (verified) runs an UPDATE of an indexed column as a delete
-- plus an insert, and the insert fails with "key is still referenced" while
-- another table references the row. These seven plain indexes sit on columns
-- the engines update on referenced rows (status/state/counter and lookup
-- columns), so every such update on DuckDB would fail. Drop them on BOTH
-- engines: Postgres keeps the lookup cost acceptable at study scale via its
-- other indexes, and one declared shape beats a per-engine index map.
-- Performance note (issue #330, out of scope): the lookups scan the table,
-- which is fast at study scale. Measure before adding an index back.
-- ============================================================================

DROP INDEX IF EXISTS idx_research_assignments_status;
DROP INDEX IF EXISTS idx_codebook_releases_state;
DROP INDEX IF EXISTS idx_documents_tenant_date;
DROP INDEX IF EXISTS idx_documents_hash;
DROP INDEX IF EXISTS idx_documents_project;
DROP INDEX IF EXISTS idx_documents_source_hash;
DROP INDEX IF EXISTS idx_chunks_hash;
