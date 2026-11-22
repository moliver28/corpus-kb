-- Rollback of DuckDB migration 046 (embedding_cache_2560, issue #539).
DROP TABLE IF EXISTS embedding_cache_2560;
