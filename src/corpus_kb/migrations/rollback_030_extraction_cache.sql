-- Rollback migration 030: drop the extraction cache.

DROP TABLE IF EXISTS extraction_cache;
