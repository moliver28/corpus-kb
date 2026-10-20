-- ============================================================================
-- Migration 011: FORCE row-level security (DuckDB mirror — dropped by design)
-- ============================================================================
-- Pair of migrations/011_force_rls.sql. It marks twelve tables FORCE ROW
-- LEVEL SECURITY so the table owner cannot bypass the tenant policies.
-- DuckDB has no row-level security. The gateway carries an explicit
-- tenant_id predicate on every statement, on both engines, so tenant
-- isolation does not depend on this flag (program doc sections 1.1 and 3.1).
-- Tables touched on Postgres: tenants, documents, chunks, chunks_vectors,
-- entities, relations, projection_checkpoints, projection_dlq,
-- idempotency_keys, tags, document_tags, metadata.
-- No statement runs here; the file keeps the 1:1 numbering.
-- ============================================================================

SELECT 1 WHERE FALSE;
