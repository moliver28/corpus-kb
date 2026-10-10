-- ============================================================================
-- Migration 019: Codebook release read models (U1-U6, v6 §6)
-- ============================================================================
-- Four tables backing the release lifecycle (the "lock layer"):
--
--   codebook_releases      one row per release; states draft_candidate |
--                          released | superseded | retired. Content lock via
--                          manifest_json + manifest_sha256 (canonical JSON,
--                          research/release/manifest.py). UNIQUE: exactly ONE
--                          released row per (tenant_id, codebook_id,
--                          codebook_version) — partial unique index on
--                          state = 'released'.
--   release_gate_results   per-gate evidence for one release (assess is
--                          repeatable: the PK upserts while the release is a
--                          draft).
--   release_waivers        human waiver per gate: justification and approver
--                          are NOT NULL and non-blank (checked).
--   audit_partitions       U9/U17 sampling ledger: scope, unit, partition
--                          (discovery|calibration|test|audit), stratum,
--                          inclusion probability, seed. UNIQUE
--                          (tenant_id, scope, unit_id): a unit lands in
--                          exactly ONE partition per sampling scope —
--                          disjointness is a constraint, not a hope. Re-sampling
--                          uses a NEW scope string (the scope carries its own
--                          design version).
--
-- IMMUTABILITY: released rows are immutable. The trigger
-- codebook_releases_immutability_guard blocks every UPDATE of a released row
-- EXCEPT the whitelisted state transitions (released -> superseded /
-- retired: state + the transition bookkeeping columns only — never manifest,
-- version identity, or approval) and blocks DELETE of released rows. Child
-- rows (gate results, waivers) freeze once the parent release is released via
-- release_children_immutability_guard, which also rejects orphans whose
-- (tenant_id, release_id) do not resolve to a visible release row — the
-- tenant-scoped equivalent of the FK.
--
-- FK SCOPE: release_gate_results/release_waivers FK into codebook_releases.
-- audit_partitions stays FK-FREE (017 rationale: plain BIGINT unit refs keep
-- rollback_016's plain DROP TABLE and the rebuild drop-set safe).
--
-- RLS: ENABLE + FORCE + per-tenant policies, mirroring migration 016's DO
-- block (DROP POLICY IF EXISTS first, per-table failure isolation).
--
-- GRANTS: none in-migration, matching every existing migration — the PG15+
-- recipe in docs/INSTALL.md (ALTER DEFAULT PRIVILEGES ... GRANT ALL ON
-- TABLES/SEQUENCES TO corpus_user) covers tables created afterwards.
--
-- event_store/snapshot_store (lib-owned) are NEVER touched by this migration.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. codebook_releases
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS codebook_releases (
    release_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    project_id UUID,
    codebook_id UUID NOT NULL,
    codebook_version UUID NOT NULL,
    codebook_version_sha256 CHAR(64) NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'draft_candidate'
        CHECK (state IN ('draft_candidate','released','superseded','retired')),
    profile TEXT NOT NULL DEFAULT 'team-codebook',
    parent_release_id UUID,
    manifest_json JSONB NOT NULL DEFAULT '{}',
    manifest_sha256 CHAR(64) NOT NULL,
    superseded_by_release_id UUID,
    superseded_at TIMESTAMPTZ,
    retired_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    released_at TIMESTAMPTZ,
    creator TEXT NOT NULL DEFAULT '',
    approver TEXT
);

-- ONE released row per codebook version (the DB-level race guard behind the
-- projector's guarded transition UPDATE).
CREATE UNIQUE INDEX IF NOT EXISTS uq_codebook_releases_released
    ON codebook_releases (tenant_id, codebook_id, codebook_version)
    WHERE state = 'released';

CREATE INDEX IF NOT EXISTS idx_codebook_releases_state
    ON codebook_releases (tenant_id, state);
CREATE INDEX IF NOT EXISTS idx_codebook_releases_codebook
    ON codebook_releases (tenant_id, codebook_id);

-- ----------------------------------------------------------------------------
-- 2. release_gate_results
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS release_gate_results (
    tenant_id UUID NOT NULL,
    release_id UUID NOT NULL REFERENCES codebook_releases(release_id) ON DELETE CASCADE,
    gate_id TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('pass','fail','not_evaluable','waived')),
    value JSONB,
    threshold JSONB,
    reason TEXT,
    evidence_refs JSONB NOT NULL DEFAULT '[]',
    evaluator JSONB NOT NULL DEFAULT '{}',
    evaluated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, release_id, gate_id)
);

-- ----------------------------------------------------------------------------
-- 3. release_waivers
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS release_waivers (
    tenant_id UUID NOT NULL,
    release_id UUID NOT NULL REFERENCES codebook_releases(release_id) ON DELETE CASCADE,
    gate_id TEXT NOT NULL,
    justification TEXT NOT NULL CHECK (btrim(justification) <> ''),
    approver TEXT NOT NULL CHECK (btrim(approver) <> ''),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, release_id, gate_id)
);

-- ----------------------------------------------------------------------------
-- 4. audit_partitions
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS audit_partitions (
    audit_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id UUID NOT NULL,
    project_id UUID,
    scope TEXT NOT NULL,
    unit_id BIGINT NOT NULL,
    partition TEXT NOT NULL
        CHECK (partition IN ('discovery','calibration','test','audit')),
    stratum TEXT,
    inclusion_probability REAL,
    sampled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    seed BIGINT,
    UNIQUE (tenant_id, scope, unit_id)
);

CREATE INDEX IF NOT EXISTS idx_audit_partitions_scope
    ON audit_partitions (tenant_id, scope, partition);
CREATE INDEX IF NOT EXISTS idx_audit_partitions_unit
    ON audit_partitions (tenant_id, unit_id);

-- ----------------------------------------------------------------------------
-- 5. Immutability triggers
-- ----------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION codebook_releases_immutability_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.state = 'released' THEN
            RAISE EXCEPTION 'released release % is immutable (delete blocked)',
                OLD.release_id;
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.state <> 'released' THEN
        RETURN NEW;
    END IF;
    -- The ONLY permitted changes to a released row are the supersede / retire
    -- STATE transitions: state + transition bookkeeping, nothing else.
    IF NEW.state NOT IN ('superseded', 'retired') THEN
        RAISE EXCEPTION 'released release % is immutable (only supersede/retire transitions)',
            OLD.release_id;
    END IF;
    IF NEW.manifest_json IS DISTINCT FROM OLD.manifest_json
        OR NEW.manifest_sha256 IS DISTINCT FROM OLD.manifest_sha256
        OR NEW.codebook_id IS DISTINCT FROM OLD.codebook_id
        OR NEW.codebook_version IS DISTINCT FROM OLD.codebook_version
        OR NEW.codebook_version_sha256 IS DISTINCT FROM OLD.codebook_version_sha256
        OR NEW.parent_release_id IS DISTINCT FROM OLD.parent_release_id
        OR NEW.profile IS DISTINCT FROM OLD.profile
        OR NEW.creator IS DISTINCT FROM OLD.creator
        OR NEW.approver IS DISTINCT FROM OLD.approver
        OR NEW.created_at IS DISTINCT FROM OLD.created_at
        OR NEW.released_at IS DISTINCT FROM OLD.released_at
        OR NEW.project_id IS DISTINCT FROM OLD.project_id
        OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id THEN
        RAISE EXCEPTION 'released release % is immutable (content edit blocked)',
            OLD.release_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_codebook_releases_immutability ON codebook_releases;
CREATE TRIGGER trg_codebook_releases_immutability
    BEFORE UPDATE OR DELETE ON codebook_releases
    FOR EACH ROW EXECUTE FUNCTION codebook_releases_immutability_guard();

CREATE OR REPLACE FUNCTION release_children_immutability_guard() RETURNS trigger AS $$
DECLARE
    parent_state TEXT;
    target_release UUID;
    target_tenant UUID;
BEGIN
    target_release := COALESCE(NEW.release_id, OLD.release_id);
    target_tenant := COALESCE(NEW.tenant_id, OLD.tenant_id);
    SELECT state INTO parent_state FROM codebook_releases
    WHERE release_id = target_release AND tenant_id = target_tenant;
    IF parent_state IS NULL THEN
        RAISE EXCEPTION 'release % not found for tenant % (tenant-scoped FK)',
            target_release, target_tenant;
    END IF;
    IF parent_state = 'released' THEN
        RAISE EXCEPTION 'release % is released; its % rows are frozen',
            target_release, TG_TABLE_NAME;
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_release_gate_results_frozen ON release_gate_results;
CREATE TRIGGER trg_release_gate_results_frozen
    BEFORE INSERT OR UPDATE OR DELETE ON release_gate_results
    FOR EACH ROW EXECUTE FUNCTION release_children_immutability_guard();

DROP TRIGGER IF EXISTS trg_release_waivers_frozen ON release_waivers;
CREATE TRIGGER trg_release_waivers_frozen
    BEFORE INSERT OR UPDATE OR DELETE ON release_waivers
    FOR EACH ROW EXECUTE FUNCTION release_children_immutability_guard();

-- ----------------------------------------------------------------------------
-- 6. RLS: ENABLE + FORCE + tenant policy on all four tables (016 pattern)
-- ----------------------------------------------------------------------------

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'codebook_releases','release_gate_results','release_waivers',
        'audit_partitions'
    ] LOOP
        BEGIN
            EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
            EXECUTE format('DROP POLICY IF EXISTS %I_tenant_isolation ON %I', t, t);
            EXECUTE format(
                'CREATE POLICY %I_tenant_isolation ON %I '
                'USING (tenant_id = NULLIF(current_setting(''app.current_tenant_id'', true), '''')::UUID) '
                'WITH CHECK (tenant_id = NULLIF(current_setting(''app.current_tenant_id'', true), '''')::UUID)',
                t, t);
            EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING 'RLS setup failed for %: %', t, SQLERRM;
        END;
    END LOOP;
END $$;
