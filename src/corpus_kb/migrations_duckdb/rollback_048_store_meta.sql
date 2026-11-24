-- Rollback of DuckDB migration 048 (store meta, issue #559 / G28).
-- Pair of migrations/rollback_047_store_meta.sql. Drops the store-level
-- key/value table (main schema); the boot guard then treats every store as
-- pre-parameterization (2560) until the next migrate run.

DROP TABLE IF EXISTS meta;
