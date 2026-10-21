-- ============================================================================
-- Migration 003: enable extensions (DuckDB mirror — dropped by design)
-- ============================================================================
-- Pair of migrations/003_enable_extensions.sql. It runs CREATE EXTENSION for
-- "vector" and "uuid-ossp". Both are Postgres-only:
--   * vector (pgvector): DuckDB stores vectors as FLOAT[n] columns and
--     searches them with exact scan or the optional vss extension, so there
--     is no pgvector type to install.
--   * uuid-ossp: DuckDB ships gen_random_uuid() in the core, so the helper
--     library is not needed.
-- No statement runs here. The file keeps the 1:1 numbering for the
-- schema-parity tooling.
-- ============================================================================

SELECT 1 WHERE FALSE;
