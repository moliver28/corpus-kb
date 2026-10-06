-- ============================================================================
-- Migration 015: Make chunk_signals upsertable; drop `detail` from its key
-- ============================================================================
-- Migration 012 marked chunk_signals append-only (like chunk_codes) and gave
-- it PRIMARY KEY (chunk_id, code_id, signal, detail, tenant_id). But
-- run_similarity_pooling (src/corpus_kb/coding/pooling.py) writes
--   detail = f"sim={similarity:.4f}"
-- as part of that key. Any event that shifts a chunk's similarity score even
-- slightly (re-embedding after a model change, re-ingesting a document, a
-- dimension migration) changes `detail`, so `ON CONFLICT DO NOTHING` never
-- fires: a new row is inserted alongside the old one instead of correcting
-- it. Because the append-only trigger also blocked UPDATE and DELETE, the
-- stale duplicate could never be corrected. Downstream, coder_dispatch.py
-- read the Tier-0 score via `ORDER BY score DESC LIMIT 1`, so it silently
-- used "the highest score this pair ever had," not the current one.
--
-- chunk_signals is a materialized pooling signal, recomputed on demand, not
-- an audit trail: nothing reads it as history, and its structurally
-- equivalent sibling table chunk_keyword_hits (also written by pooling.py,
-- also idempotent-by-recompute) already follows the "one current row,
-- fully mutable" pattern -- it excludes its own volatile value (n_hits) from
-- its primary key and carries NO append-only trigger at all, so a recompute
-- can freely update or delete a stale row. This migration brings
-- chunk_signals in line with that precedent instead of treating chunk_codes'
-- full-history append-only semantics (which pass + coder correctly makes
-- historical) as the template.
--
-- A DELETE-capable table also matters for the second-order coverage bug
-- fixed alongside this: reconcile_coverage recomputes chunk_status.in_any_pool
-- from "does a chunk_signals row currently exist for this chunk", so when a
-- codebook narrows or a pool_floor is raised and a chunk/code pair no longer
-- qualifies, run_similarity_pooling must be able to delete that pair's stale
-- row for the flag to correctly flip back to false. An append-only-on-delete
-- table could never let that happen.
--
-- Fix: drop `detail` from the key so the natural key is
-- (chunk_id, code_id, signal, tenant_id) -- one current row per
-- chunk/code/signal-type -- and drop the append-only trigger entirely, so
-- pooling.py's ON CONFLICT ... DO UPDATE and its DELETE-when-no-longer-
-- qualifying path both work.
--
-- Pre-existing duplicates (minted by the pre-fix code path) are collapsed
-- first, keeping the MOST RECENTLY WRITTEN row per (chunk_id, code_id,
-- signal, tenant_id) -- the freshest recomputation is the one most likely to
-- reflect the current embedding, not whichever row ever scored highest.
--
-- FORCE row-level security (migration 013) applies to the table OWNER, which
-- is the role that runs this migration, so the cleanup below would
-- otherwise only see whichever single tenant app.current_tenant_id happens
-- to name. FORCE is lifted for the cleanup and restored in the same
-- transaction. The append-only trigger is dropped BEFORE the cleanup DELETEs
-- (not DISABLEd and re-dropped later): the trigger does not exist yet on a
-- first run, so an unconditional DISABLE would abort the migration, and a
-- DROP TRIGGER IF EXISTS up front is both unblocking and idempotent on
-- re-apply.
-- ============================================================================

ALTER TABLE chunk_signals NO FORCE ROW LEVEL SECURITY;

DROP TRIGGER IF EXISTS chunk_signals_append_only_trigger ON chunk_signals;

CREATE TEMP TABLE chunk_signals_dupes ON COMMIT DROP AS
WITH keepers AS (
    SELECT DISTINCT ON (chunk_id, code_id, signal, tenant_id) ctid AS keeper_ctid
    FROM chunk_signals
    ORDER BY chunk_id, code_id, signal, tenant_id, created_at DESC
)
SELECT cs.ctid AS dupe_ctid
FROM chunk_signals cs
WHERE cs.ctid NOT IN (SELECT keeper_ctid FROM keepers);

DELETE FROM chunk_signals
WHERE ctid IN (SELECT dupe_ctid FROM chunk_signals_dupes);

ALTER TABLE chunk_signals DROP CONSTRAINT IF EXISTS chunk_signals_pkey;

ALTER TABLE chunk_signals
    ADD CONSTRAINT chunk_signals_pkey PRIMARY KEY (chunk_id, code_id, signal, tenant_id);

DROP FUNCTION IF EXISTS chunk_signals_append_only();

ALTER TABLE chunk_signals FORCE ROW LEVEL SECURITY;
