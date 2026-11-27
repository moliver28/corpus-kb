-- ============================================================================
-- Migration 051: backfill pre-W3 web source_type rows to the canonical name
-- (issue #591)
-- ============================================================================
-- W3 (the typed source_types registry) stamps the canonical name
-- external-site at ingest; pre-W3 web ingests stored the legacy alias web.
-- The query-side resolver (aa975c13) maps the legacy name at the predicate,
-- but stored rows stay web until a data backfill. W5 typed filters assume
-- canonical rows.
--
-- Repair: one UPDATE on documents. It is the only table that stores the
-- document source_type; chunks carry per-chunk types (text, code, markdown)
-- and never web.
--
-- Idempotent: the predicate matches nothing once every row is canonical.
--
-- RLS: documents is FORCE RLS on a real install (011), and the migration
-- runs as the table owner, which RLS also binds. The owner must see past
-- RLS for the backfill, so the block captures the table's FORCE flag FIRST,
-- drops FORCE for the run, and restores the CAPTURED state afterwards: a
-- database that never took 011's FORCE keeps it that way. This mirrors the
-- migration 044 pattern.
-- ============================================================================

DO $$
DECLARE
    docs_force BOOLEAN;
BEGIN
    SELECT relforcerowsecurity INTO docs_force
        FROM pg_class WHERE oid = 'documents'::regclass;

    EXECUTE 'ALTER TABLE documents NO FORCE ROW LEVEL SECURITY';

    UPDATE documents
    SET source_type = 'external-site'
    WHERE source_type = 'web';

    IF docs_force THEN
        EXECUTE 'ALTER TABLE documents FORCE ROW LEVEL SECURITY';
    END IF;
END $$;
