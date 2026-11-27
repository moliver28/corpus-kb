-- Migration 052 (DuckDB twin of pg 051): backfill pre-W3 web source_type
-- rows to the canonical name (issue #591).
--
-- W3 stamps the canonical external-site at ingest; pre-W3 web ingests
-- stored the legacy alias web. DuckDB documents rows carry the same stamp,
-- so the mirror runs the same one UPDATE.
--
-- Idempotent: the predicate matches nothing once every row is canonical.
-- Data repair: no schema shape to reverse (see the pg rollback note).
UPDATE documents SET source_type = 'external-site' WHERE source_type = 'web';
