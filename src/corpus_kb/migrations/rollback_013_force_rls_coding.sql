-- ============================================================================
-- Migration 013 Rollback: Un-force RLS on the coding-domain tables
-- ============================================================================
-- Lifts FORCE ROW LEVEL SECURITY from the twelve coding tables, leaving the
-- ENABLE + tenant_isolation policies from migration 012 in place (the state
-- that existed between migrations 012 and 013). Applications connecting as
-- the owning role regain the owner-bypass this migration closed; every other
-- role is still policy-scoped.
--
-- Each ALTER TABLE is wrapped in a DO block so the rollback is resilient when
-- a table has already been dropped (e.g. by rollback_012). This rollback
-- exists because, unlike the table-owning FORCE flags that rollback_012
-- removes with the tables themselves, lifting FORCE here is the minimal
-- reverse of 013 without touching any data.

DO $$
BEGIN
    ALTER TABLE IF EXISTS code_registry NO FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not un-force RLS on code_registry: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS codebook_versions NO FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not un-force RLS on codebook_versions: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS code_keywords NO FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not un-force RLS on code_keywords: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS chunk_signals NO FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not un-force RLS on chunk_signals: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS chunk_keyword_hits NO FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not un-force RLS on chunk_keyword_hits: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS answer_ctx_vectors NO FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not un-force RLS on answer_ctx_vectors: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS batch_status NO FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not un-force RLS on batch_status: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS chunk_codes NO FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not un-force RLS on chunk_codes: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS final_codes NO FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not un-force RLS on final_codes: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS chunk_status NO FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not un-force RLS on chunk_status: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS doc_extraction_audit NO FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not un-force RLS on doc_extraction_audit: %', SQLERRM;
END $$;

DO $$
BEGIN
    ALTER TABLE IF EXISTS coding_gate_decisions NO FORCE ROW LEVEL SECURITY;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Could not un-force RLS on coding_gate_decisions: %', SQLERRM;
END $$;
