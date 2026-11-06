-- ============================================================================
-- Rollback migration 042 (DuckDB): recreate the 7 dropped lookup indexes.
-- ============================================================================
-- Pair of migrations/rollback_041_inplace_update_indexes.sql; the original
-- CREATE INDEX statements verbatim (016, 019, 027, 000, 007). Caveat: on
-- DuckDB, updates of these indexed columns on referenced rows fail again
-- after this rollback.
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_research_assignments_status
    ON research_assignments(tenant_id, status);

CREATE INDEX IF NOT EXISTS idx_codebook_releases_state
    ON codebook_releases (tenant_id, state);

CREATE INDEX IF NOT EXISTS idx_documents_tenant_date
    ON documents(tenant_id, document_date);

CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(file_hash);

CREATE INDEX IF NOT EXISTS idx_documents_project ON documents(project_id);

CREATE INDEX IF NOT EXISTS idx_documents_source_hash ON documents(source_hash);

CREATE INDEX IF NOT EXISTS idx_chunks_hash ON chunks(chunk_hash);
