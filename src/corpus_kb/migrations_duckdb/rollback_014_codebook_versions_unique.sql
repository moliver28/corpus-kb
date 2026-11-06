-- ============================================================================
-- Rollback migration 014 (DuckDB): drop the codebook_versions unique index.
-- ============================================================================
-- Pair of migrations/rollback_014_codebook_versions_unique.sql. The unique
-- key is an INDEX here (DuckDB cannot add a table constraint after CREATE
-- TABLE), so the rollback drops the index. The deduplicated rows stay
-- deduplicated, exactly like the Postgres rollback.
-- ============================================================================

DROP INDEX IF EXISTS codebook_versions_tenant_sha256_key;
