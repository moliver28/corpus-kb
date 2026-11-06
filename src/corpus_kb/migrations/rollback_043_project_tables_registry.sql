-- Rollback 043: drop the project_tables registry (the runtime raw_<slug>
-- tables are factory-owned; drop them through project-drop, not here).

DROP TABLE IF EXISTS project_tables;
