-- ============================================================================
-- Migration 045: entity-id aliases — one id space for graph entities (#455)
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
-- Idempotent: CREATE TABLE IF NOT EXISTS. Existing stores heal through NEW
-- Entity.Created events only (the projection checkpoint sits past the old
-- ones, and the event store is append-only); relation events already parked
-- in the DLQ stay manual — the retry rule is written up on issue #455.
--
-- RLS: tenant-scoped projection table; the 012/013 convention applies the
-- tenant-isolation policy and FORCEs RLS here.
-- ============================================================================

CREATE TABLE IF NOT EXISTS entity_id_aliases (
    tenant_id UUID NOT NULL,
    alias_entity_id UUID NOT NULL,
    entity_id UUID NOT NULL REFERENCES entities(entity_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, alias_entity_id)
);

CREATE INDEX IF NOT EXISTS idx_entity_aliases_entity ON entity_id_aliases(entity_id);

DO $$
BEGIN
    EXECUTE 'ALTER TABLE entity_id_aliases ENABLE ROW LEVEL SECURITY';
    EXECUTE 'DROP POLICY IF EXISTS entity_id_aliases_tenant_isolation ON entity_id_aliases';
    EXECUTE 'CREATE POLICY entity_id_aliases_tenant_isolation ON entity_id_aliases '
            'USING (tenant_id = NULLIF(current_setting(''app.current_tenant_id'', true), '''')::UUID) '
            'WITH CHECK (tenant_id = NULLIF(current_setting(''app.current_tenant_id'', true), '''')::UUID)';
    EXECUTE 'ALTER TABLE entity_id_aliases FORCE ROW LEVEL SECURITY';
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'RLS setup failed for entity_id_aliases: %', SQLERRM;
END $$;
