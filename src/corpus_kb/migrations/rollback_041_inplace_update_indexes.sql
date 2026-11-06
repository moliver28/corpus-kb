-- ============================================================================
-- Rollback migration 041: recreate the 7 dropped lookup indexes.
-- ============================================================================
-- Re-applies the original CREATE INDEX statements verbatim (016, 019, 027,
-- storage/schema.sql, 007). Rolling forward again (041) drops them again.
-- Note the pre-issue caveat: on DuckDB, updates of these indexed columns on
-- referenced rows fail again after this rollback.
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_research_assignments_status
    ON research_assignments(tenant_id, status);

CREATE INDEX IF NOT EXISTS idx_codebook_releases_state
    ON codebook_releases (tenant_id, state);

CREATE INDEX IF NOT EXISTS idx_documents_tenant_date
    ON documents(tenant_id, document_date);

CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(file_hash);

CREATE INDEX IF NOT EXISTS idx_documents_project ON documents(project_id)
    WHERE project_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_documents_source_hash ON documents(source_hash)
    WHERE source_hash IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_chunks_hash ON chunks(chunk_hash);
