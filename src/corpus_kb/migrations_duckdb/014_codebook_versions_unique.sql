-- ============================================================================
-- Migration 014: UNIQUE (tenant_id, sha256) on codebook_versions (DuckDB)
-- ============================================================================
-- Pair of migrations/014_codebook_versions_unique.sql. The Postgres file
-- collapses pre-existing duplicate codebook_versions rows first (a temp
-- table, DISTINCT ON, and calibration-floor carry-over), then adds the
-- constraint. A fresh DuckDB install reaches this file with an empty table,
-- so the cleanup has nothing to do; the unique key itself becomes a unique
-- index (DuckDB cannot add a table constraint after CREATE TABLE).
-- codebook_loader's ON CONFLICT (tenant_id, sha256) DO NOTHING targets this
-- index by its columns.
-- ============================================================================

CREATE UNIQUE INDEX IF NOT EXISTS codebook_versions_tenant_sha256_key
    ON codebook_versions (tenant_id, sha256);
