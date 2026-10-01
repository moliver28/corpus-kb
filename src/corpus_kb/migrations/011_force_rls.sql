-- ============================================================================
-- Migration 011: FORCE row-level security on every tenant-scoped table
-- ============================================================================
-- ENABLE ROW LEVEL SECURITY (set in src/storage/schema.sql) does not apply to
-- a table's OWNER: Postgres exempts the owning role from RLS policies unless
-- the table is also marked FORCE. The application connects as corpus_user, and
-- the documented install path runs scripts/migrate.py as corpus_user, which
-- makes corpus_user the owner of these tables. Without FORCE, that owning
-- connection bypasses the tenant_isolation policies entirely and cross-tenant
-- reads/writes succeed.
--
-- FORCE closes that gap so tenant isolation holds regardless of who owns the
-- tables. Every data path already sets app.current_tenant_id via
-- tenant_connection() before querying, so forcing RLS changes nothing for
-- correctly-scoped queries; it only stops the owner-bypass.
--
-- Each ALTER TABLE is wrapped in a DO block so the migration remains
-- idempotent and resilient: FORCE is itself a no-op when already set, and the
-- EXCEPTION handler lets the migration continue if a table does not exist on
-- a given deployment.
-- ============================================================================

DO $$
BEGIN
    ALTER TABLE IF EXISTS tenants FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not force RLS on tenants: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS documents FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not force RLS on documents: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS chunks FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not force RLS on chunks: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS chunks_vectors FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not force RLS on chunks_vectors: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS entities FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not force RLS on entities: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS relations FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not force RLS on relations: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS projection_checkpoints FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not force RLS on projection_checkpoints: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS projection_dlq FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not force RLS on projection_dlq: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS idempotency_keys FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not force RLS on idempotency_keys: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS tags FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not force RLS on tags: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS document_tags FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not force RLS on document_tags: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS metadata FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not force RLS on metadata: %', SQLERRM;
END $$;
