-- ============================================================================
-- Migration 045 (DuckDB): entity-id aliases — one id space for graph
-- entities (#455). Twin of migrations/045_entity_id_aliases.sql.
-- ============================================================================
-- The entities read model dedupes on (tenant_id, name, entity_type), so the
-- FIRST projected aggregate uuid becomes the canonical entity_id. A later
-- add-entity with the same name mints a NEW aggregate uuid — the id the CLI
-- returns — that no read-model row carries. A relation built from that id
-- then violated relations' FK to entities and landed in the projection DLQ
-- (issue #455, Finding 1), and the relation never materialized.
--
-- Fix: every later aggregate id becomes an ALIAS row that points at the
-- canonical entity. DocumentsProjection writes the aliases when an entity
-- insert dedupes (projections/documents_rows.py) and resolves relation
-- endpoints through them, so the FK only ever sees ids the entity table
-- carries. Graph reads resolve alias ids to the canonical row, so hops and
-- search answer any id a caller ever received.
--
-- DuckDB keeps entity ids as VARCHAR (canonical uuid text), and there is no
-- RLS on DuckDB (see the 011 twin note). The alias table declares NO foreign
-- key to entities: migration 041 rebuilds entities through DROP TABLE, and a
-- key table here would block that replay (migrations apply twice cleanly).
-- Nothing deletes entities rows today, so the pg-side FK action has no
-- runtime role; the drift is declared in scripts/schema_parity_gate.py.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS. Existing stores heal through NEW
-- Entity.Created events only (the projection checkpoint sits past the old
-- ones, and the event store is append-only); relation events already parked
-- in the DLQ stay manual — the retry rule is written up on issue #455.
-- ============================================================================

CREATE TABLE IF NOT EXISTS entity_id_aliases (
    tenant_id VARCHAR NOT NULL,
    alias_entity_id VARCHAR NOT NULL,
    entity_id VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, alias_entity_id)
);

CREATE INDEX IF NOT EXISTS idx_entity_aliases_entity ON entity_id_aliases(entity_id);
