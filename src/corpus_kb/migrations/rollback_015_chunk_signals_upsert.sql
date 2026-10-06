-- ============================================================================
-- Migration 015 Rollback: Restore chunk_signals' original append-only key
-- ============================================================================
-- Reverts the primary key to (chunk_id, code_id, signal, detail, tenant_id)
-- and restores the original append-only (UPDATE + DELETE blocking) trigger.
--
-- The duplicate rows migration 015 collapsed are not resurrected. Rolling
-- this back re-opens the original bug: src/corpus_kb/coding/pooling.py's
-- ON CONFLICT (chunk_id, code_id, signal, tenant_id) DO UPDATE will fail with
-- "no unique or exclusion constraint matching the ON CONFLICT specification"
-- once the key reverts, and its DELETE-when-unqualified path will fail
-- against the restored append-only trigger. Roll back
-- src/corpus_kb/coding/pooling.py and src/corpus_kb/coding/coder_dispatch.py
-- with it.

ALTER TABLE chunk_signals NO FORCE ROW LEVEL SECURITY;

ALTER TABLE chunk_signals DROP CONSTRAINT IF EXISTS chunk_signals_pkey;

ALTER TABLE chunk_signals
    ADD CONSTRAINT chunk_signals_pkey PRIMARY KEY (chunk_id, code_id, signal, detail, tenant_id);

CREATE OR REPLACE FUNCTION chunk_signals_append_only() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'chunk_signals is append-only: UPDATE and DELETE are not permitted';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS chunk_signals_append_only_trigger ON chunk_signals;
CREATE TRIGGER chunk_signals_append_only_trigger
    BEFORE UPDATE OR DELETE ON chunk_signals
    FOR EACH ROW EXECUTE FUNCTION chunk_signals_append_only();

ALTER TABLE chunk_signals FORCE ROW LEVEL SECURITY;
