-- DuckDB twin of pg migration 044 (issue #363 ledger repair).
-- Same data repair; DuckDB has no RLS, so the FORCE dance is pg-only.

UPDATE ingested_files AS lf
SET doc_id = d.doc_id
FROM documents AS d
WHERE lf.tenant_id = d.tenant_id
  AND lf.path = d.source
  AND lf.doc_id IS NOT NULL
  AND lf.doc_id <> d.doc_id;
