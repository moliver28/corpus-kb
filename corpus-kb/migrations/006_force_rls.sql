-- ============================================================================
-- Migration 006: FORCE row-level security on every tenant-scoped table
-- ============================================================================
-- ENABLE ROW LEVEL SECURITY (set in 004) does not apply to a table's OWNER:
-- Postgres exempts the owning role from RLS policies unless the table is also
-- marked FORCE. The application connects as corpus_user, and the documented
-- install path runs scripts/migrate.py as corpus_user, which makes corpus_user
-- the owner of these tables. Without FORCE, that owning connection bypasses the
-- tenant_isolation policies entirely and cross-tenant reads/writes succeed.
--
-- FORCE closes that gap so tenant isolation holds regardless of who owns the
-- tables. Every data path already sets app.current_tenant_id via
-- tenant_connection() before querying, so forcing RLS changes nothing for
-- correctly-scoped queries; it only stops the owner-bypass. ALTER ... FORCE is
-- idempotent, so re-running is a no-op.
-- ============================================================================

ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
ALTER TABLE documents FORCE ROW LEVEL SECURITY;
ALTER TABLE chunks FORCE ROW LEVEL SECURITY;
ALTER TABLE chunks_vectors FORCE ROW LEVEL SECURITY;
ALTER TABLE entities FORCE ROW LEVEL SECURITY;
ALTER TABLE relations FORCE ROW LEVEL SECURITY;
ALTER TABLE projection_checkpoints FORCE ROW LEVEL SECURITY;
ALTER TABLE projection_dlq FORCE ROW LEVEL SECURITY;
ALTER TABLE idempotency_keys FORCE ROW LEVEL SECURITY;
ALTER TABLE tags FORCE ROW LEVEL SECURITY;
ALTER TABLE document_tags FORCE ROW LEVEL SECURITY;
ALTER TABLE metadata FORCE ROW LEVEL SECURITY;
