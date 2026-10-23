-- Rollback (DuckDB) for 031: drop the extraction cache twin.

DROP TABLE IF EXISTS extraction_cache;
