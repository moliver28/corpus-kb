-- Rollback 045 (DuckDB twin): the alias rows are derived state — they
-- rebuild from the event store the next time an Entity.Created event for
-- an already-projected name arrives. Dropping the table removes the alias
-- id space without touching entities or relations; id resolution falls
-- back to the raw id, which is the pre-045 behavior.
DROP TABLE IF EXISTS entity_id_aliases;
