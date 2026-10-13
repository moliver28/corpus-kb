-- ============================================================================
-- Migration 023: transcript qa_context (ingestion strategy registry, S4)
-- ============================================================================
-- chunks.qa_context JSONB (NULL for every non-transcript chunk) carries the
-- transcript strategy's per-chunk Q+A provenance:
--   {"question": "...", "participants": [...], "exchange_id": "exchange-0007"}
-- The GIN index serves exchange-scoped jsonb probes and the future
-- exchange-expansion retrieval leg. It costs nothing while the column is
-- all-NULL (transcript ingests simply have not run yet).
--
-- FTS LOCKSTEP IS OPT-IN (config search.qa_context_in_fts, DEFAULT FALSE).
-- Why not on by default: Postgres ts_rank has no IDF term, so stuffing the
-- highly repetitive question text into the shared FTS index would unsoundly
-- skew ranking for the whole corpus — every "Can you tell me about ..."
-- transcript would inflate matches for its question wording. The query layer
-- reads search.qa_context_in_fts at query time; the matching expression
-- index below is created ONLY when the operator opts in at the database or
-- role level, then re-runs this migration (idempotent, migration 009's
-- DO-block pattern with a setting guard):
--
--   ALTER ROLE corpus_user SET search.qa_context_in_fts = 'on';
--   -- re-run this migration; idx_chunks_fts_qa_context appears
--
-- RLS/grants: chunks already carries ENABLE+FORCE row level security
-- (migration 011) and ALTER TABLE does not change ownership, so no policy
-- or grant work belongs here.
-- Idempotent: ADD COLUMN IF NOT EXISTS + CREATE INDEX IF NOT EXISTS.
-- ============================================================================

SET app.current_tenant_id = '00000000-0000-0000-0000-000000000001';

ALTER TABLE chunks ADD COLUMN IF NOT EXISTS qa_context JSONB;

-- ----------------------------------------------------------------------------
-- 1. Exchange/qa-scoped jsonb lookups (always created; inert until used)
-- ----------------------------------------------------------------------------

DO $$
BEGIN
  EXECUTE 'CREATE INDEX IF NOT EXISTS idx_chunks_qa_context '
          'ON chunks USING gin (qa_context)';
EXCEPTION WHEN OTHERS THEN
  RAISE WARNING 'Skipping idx_chunks_qa_context: %', SQLERRM;
END $$;

-- ----------------------------------------------------------------------------
-- 2. Opt-in FTS lockstep: question text folded into the FTS expression.
-- Only maintained when search.qa_context_in_fts is explicitly 'on' (a bare
-- current_setting returns NULL -> treated as off, the documented default).
-- ----------------------------------------------------------------------------

DO $$
BEGIN
  IF coalesce(current_setting('search.qa_context_in_fts', true), 'off') = 'on' THEN
    EXECUTE 'CREATE INDEX IF NOT EXISTS idx_chunks_fts_qa_context ON chunks USING gin '
            '(to_tsvector(''english'', coalesce(text, '''') || '' '' || '
            'coalesce(qa_context->>''question'', '''')))';
  ELSE
    RAISE NOTICE 'search.qa_context_in_fts is off (default): '
                 'skipping idx_chunks_fts_qa_context';
  END IF;
EXCEPTION WHEN OTHERS THEN
  RAISE WARNING 'Skipping idx_chunks_fts_qa_context: %', SQLERRM;
END $$;
