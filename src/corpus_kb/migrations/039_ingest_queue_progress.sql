-- ============================================================================
-- Migration 039: the ingest queue progress counters (textbook program, W2C)
-- ============================================================================
-- Three nullable columns on the existing ingest_queue table. The Wave 2C
-- worker fills them from the pipeline result when a book finishes; the
-- queue_status payload tolerates NULLs, so absence stays honest.
--
--   * pages_done   — pages processed so far (NULL: unknown this wave)
--   * pages_total  — the book's page count (NULL: unknown this wave)
--   * chunks       — chunks stored by the pipeline run
--
-- The pipeline result exposes a chunk count but no page counts today,
-- so the worker writes `chunks` and leaves both page columns NULL
-- (queue_payload answers NULL for them either way).
--
-- Idempotent: ADD COLUMN IF NOT EXISTS. No backfill: rows queued before
-- this wave keep NULL counters.
-- ============================================================================

ALTER TABLE ingest_queue ADD COLUMN IF NOT EXISTS pages_done INTEGER;

ALTER TABLE ingest_queue ADD COLUMN IF NOT EXISTS pages_total INTEGER;

ALTER TABLE ingest_queue ADD COLUMN IF NOT EXISTS chunks INTEGER;
