-- Rollback of migration 047 (store meta, issue #559 / G28).
-- Drops the store-level key/value table; the boot guard then treats every
-- store as pre-parameterization (2560) until the next migrate run.

DROP TABLE IF EXISTS corpus.meta;
