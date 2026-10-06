-- ============================================================================
-- Migration 013: FORCE row-level security on the coding-domain tables
-- ============================================================================
-- Migration 012 creates twelve coding tables with ENABLE ROW LEVEL SECURITY,
-- which (exactly as with the core tables that master's 011_force_rls had to
-- fix) does not apply to a table's OWNER: Postgres exempts the owning role
-- from RLS policies unless the table is also marked FORCE. The application
-- connects as corpus_user, and the documented install path runs
-- src/corpus_kb/_setup/migrate.py as corpus_user, which makes corpus_user the
-- owner of these tables. Without FORCE, that owning connection bypasses the
-- tenant_isolation policies entirely and cross-tenant reads/writes succeed.
--
-- FORCE closes that gap so tenant isolation holds regardless of who owns the
-- tables. Every coding data path already sets app.current_tenant_id via
-- tenant_connection() before querying, so forcing RLS changes nothing for
-- correctly-scoped queries; it only stops the owner-bypass.
--
-- Each ALTER TABLE is wrapped in a DO block so the migration remains
-- idempotent and resilient, mirroring master's 011_force_rls: FORCE is itself
-- a no-op when already set, and the EXCEPTION handler lets the migration
-- continue if a table does not exist on a given deployment.

DO $$
BEGIN
    ALTER TABLE IF EXISTS code_registry FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not force RLS on code_registry: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS codebook_versions FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not force RLS on codebook_versions: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS code_keywords FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not force RLS on code_keywords: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS chunk_signals FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not force RLS on chunk_signals: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS chunk_keyword_hits FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not force RLS on chunk_keyword_hits: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS answer_ctx_vectors FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not force RLS on answer_ctx_vectors: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS batch_status FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not force RLS on batch_status: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS chunk_codes FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not force RLS on chunk_codes: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS final_codes FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not force RLS on final_codes: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS chunk_status FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not force RLS on chunk_status: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS doc_extraction_audit FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not force RLS on doc_extraction_audit: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS coding_gate_decisions FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not force RLS on coding_gate_decisions: %', SQLERRM;
END $$;
