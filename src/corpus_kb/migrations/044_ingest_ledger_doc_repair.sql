-- ============================================================================
-- Migration 044: re-point the ingest ledger at the real document (issue #363)
-- ============================================================================
-- Before the reuse fix, a re-ingest minted a NEW document id while the
-- documents read tables upsert on (tenant_id, source) and kept the FIRST
-- id. The ledger row was then re-pointed at the new id — one no documents
-- row carries — so child writes failed their foreign keys and landed in
-- the projection DLQ, and queries kept serving the stale units.
--
-- Repair: the ledger follows the documents row for the same (tenant_id,
-- source). Rows whose stored path string differs from the documents
-- source (a caller passed a relative path) cannot be matched and stay
-- as they are. Such a row still names a phantom id. The ingest boundary
-- now resolves paths, so the next ingest of that file keys the resolved
-- spelling; its ledger probe misses once, then the new resolved row
-- keeps every later ingest on one aggregate.
--
-- Idempotent: the join matches nothing once the ids agree.
--
-- RLS: documents (011) and ingested_files (016) are FORCE RLS on a real
-- install and the migration runs as the table owner, which RLS also
-- binds. The owner must see past RLS for the repair, so the block reads
-- each table's FORCE flag FIRST, drops FORCE for the run, and restores
-- the CAPTURED state afterwards: a database that never took 011/016's
-- FORCE (test harnesses skip those migrations) keeps it that way.
-- ============================================================================

DO $$
DECLARE
    docs_force BOOLEAN;
    ledger_force BOOLEAN;
BEGIN
    SELECT relforcerowsecurity INTO docs_force
        FROM pg_class WHERE oid = 'documents'::regclass;
    SELECT relforcerowsecurity INTO ledger_force
        FROM pg_class WHERE oid = 'ingested_files'::regclass;

    EXECUTE 'ALTER TABLE documents NO FORCE ROW LEVEL SECURITY';
    EXECUTE 'ALTER TABLE ingested_files NO FORCE ROW LEVEL SECURITY';

    UPDATE ingested_files AS lf
    SET doc_id = d.doc_id
    FROM documents AS d
    WHERE lf.tenant_id = d.tenant_id
      AND lf.path = d.source
      AND lf.doc_id IS NOT NULL
      AND lf.doc_id <> d.doc_id;

    IF docs_force THEN
        EXECUTE 'ALTER TABLE documents FORCE ROW LEVEL SECURITY';
    END IF;
    IF ledger_force THEN
        EXECUTE 'ALTER TABLE ingested_files FORCE ROW LEVEL SECURITY';
    END IF;
END $$;
