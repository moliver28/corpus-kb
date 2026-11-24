"""The declared-drift allowlist for the schema-parity gate (issue #127).

The gate (scripts/schema_parity_gate.py) diffs the folded Postgres truth
against the folded DuckDB mirror; every real difference must appear here
with a one-line why. An entry that no longer matches real drift is STALE
and fails the gate, so the table cannot outlive its drift.

STATIC DATA ONLY. sqlglot is static analysis in this program (storage
portability program, OSS table; section 17).
"""

from __future__ import annotations

_NUMERIC_TO_DOUBLE = "Unbounded numeric becomes DOUBLE on DuckDB (duckdb 012 header)."
_DYNAMIC_RETYPE = (
    "pg 025 retypes this to halfvec(2560) via dynamic EXECUTE; the static fold cannot see it."
)
_NOT_NULL_ADD = "DuckDB refuses NOT NULL ADD COLUMN; the gateway coalesces NULL."
_DUCK_FK_ACTIONS = (
    "DuckDB cannot declare ON DELETE actions (duckdb 000 header); the"
    " plain FK holds and Python delete paths enforce the action."
)
#: (kind, table, item) -> one-line why. Anything not listed here fails.
ALLOWED_DRIFT: dict[tuple[str, str, str], str] = {
    # -- Unbounded numeric -> DOUBLE (duckdb 012 header; W0 spike finding).
    ("column.type", "chunk_codes", "confidence"): _NUMERIC_TO_DOUBLE,
    ("column.type", "chunk_signals", "score"): _NUMERIC_TO_DOUBLE,
    ("column.type", "code_keywords", "ll"): _NUMERIC_TO_DOUBLE,
    ("column.type", "code_keywords", "log_ratio"): _NUMERIC_TO_DOUBLE,
    ("column.type", "code_registry", "pool_floor"): _NUMERIC_TO_DOUBLE,
    ("column.type", "code_registry", "residual_floor"): _NUMERIC_TO_DOUBLE,
    ("column.type", "doc_extraction_audit", "miss_rate_point"): _NUMERIC_TO_DOUBLE,
    ("column.type", "doc_extraction_audit", "miss_rate_ub95"): _NUMERIC_TO_DOUBLE,
    ("column.type", "final_codes", "confidence"): _NUMERIC_TO_DOUBLE,
    # -- pg 025 retypes these to halfvec(2560) with dynamic EXECUTE format;
    # -- the static fold cannot see inside dynamic SQL.
    ("column.type", "answer_ctx_vectors", "vector"): _DYNAMIC_RETYPE,
    ("column.type", "chunk_codes", "rationale_vector"): _DYNAMIC_RETYPE,
    ("column.type", "code_registry", "definition_vector"): _DYNAMIC_RETYPE,
    ("column.type", "code_registry", "probe_vector"): _DYNAMIC_RETYPE,
    # -- DuckDB refuses NOT NULL on ADD COLUMN; the gateway coalesces NULL.
    ("column.nullable", "chunks", "degraded"): _NOT_NULL_ADD,
    ("column.nullable", "chunks_vectors", "degraded"): _NOT_NULL_ADD,
    ("column.nullable", "documents", "doc_version"): _NOT_NULL_ADD,
    ("column.nullable", "answer_ctx_vectors", "vector"): (
        "pg 025 drops this NOT NULL via dynamic EXECUTE; both engines end nullable."
    ),
    # -- Constraint shape differences with a documented owner.
    ("unique", "codebook_releases", "codebook_id,codebook_version,tenant_id"): (
        "pg keeps the one-released-row unique; DuckDB uses a plain index, gateway enforced."
    ),
    ("unique", "relations", "relation_type,source_entity_id,target_entity_id,tenant_id"): (
        "pg 008 replaces this unique in a DO block; the gate cannot attribute the drop."
    ),
    # -- DuckDB-only ledger column and index (duckdb 030, documented there).
    ("column", "ingest_runs", "source"): (
        "DuckDB-only ledger column (030): documents rows freeze, so freshness lives in the ledger."
    ),
    ("index", "ingest_runs", "source,started_at,tenant_id"): "DuckDB-only ledger index (030).",
    ("index", "codebook_releases", "codebook_id,codebook_version,tenant_id"): (
        "Plain lookup twin of the pg unique index; the gateway enforces the rule."
    ),
    # -- Orphan sequence kept for the pg serial naming convention.
    ("sequence", "", "sources_id_seq"): (
        "Orphan: DuckDB 001 mirrors the pg serial name; sources.id uses gen_random_uuid()."
    ),
    # -- entities.source_document_id FK (issue #330): Postgres drops it in
    # -- its 040 via ALTER TABLE DROP CONSTRAINT, which the static fold
    # -- cannot see; the DuckDB rebuild (041) lands the same end state.
    # -- storage.duckdb_cascade owns the SET NULL action on both engines.
    ("fk", "entities", "source_document_id"): (
        "Dropped by pg 040 (ALTER DROP CONSTRAINT is invisible to the fold);"
        " DuckDB 041 rebuilds entities without it. The cascade module owns"
        " the SET NULL action."
    ),
    # -- entity_id_aliases.entity_id FK (issue #455): only the pg mirror
    # -- declares it. DuckDB 041 rebuilds entities through DROP TABLE, and
    # -- a key table here would block that replay (migrations apply twice
    # -- cleanly). Nothing deletes entities rows, so the action has no
    # -- runtime role on DuckDB.
    ("fk", "entity_id_aliases", "entity_id"): (
        "pg 045 only: DuckDB 045 skips the FK because the 041 entities"
        " rebuild (DROP TABLE) must stay replayable."
    ),
    # -- FK referential actions (W4 audit G9): DuckDB cannot declare ON
    # -- DELETE actions at all (duckdb 000 header), so every Postgres
    # -- action FK pairs with a plain mirror FK. Delete paths in Python
    # -- (storage.duckdb_cascade and its callers) own the action.
    ("fk", "belief_revisions", "belief_id"): _DUCK_FK_ACTIONS,
    ("fk", "chunk_labels", "chunk_id"): _DUCK_FK_ACTIONS,
    ("fk", "chunk_predictions", "chunk_id"): _DUCK_FK_ACTIONS,
    ("fk", "chunk_predictions", "set_id"): _DUCK_FK_ACTIONS,
    ("fk", "chunk_topics", "chunk_id"): _DUCK_FK_ACTIONS,
    ("fk", "chunk_topics", "topic_id"): _DUCK_FK_ACTIONS,
    ("fk", "chunks", "doc_id"): _DUCK_FK_ACTIONS,
    ("fk", "chunks_vectors", "chunk_id"): _DUCK_FK_ACTIONS,
    ("fk", "document_tags", "doc_id"): _DUCK_FK_ACTIONS,
    ("fk", "document_tags", "tag_id"): _DUCK_FK_ACTIONS,
    ("fk", "metadata", "doc_id"): _DUCK_FK_ACTIONS,
    ("fk", "nodes", "source_id"): _DUCK_FK_ACTIONS,
    ("fk", "relations", "chunk_id"): _DUCK_FK_ACTIONS,
    ("fk", "relations", "source_entity_id"): _DUCK_FK_ACTIONS,
    ("fk", "relations", "target_entity_id"): _DUCK_FK_ACTIONS,
    ("fk", "release_gate_results", "release_id"): _DUCK_FK_ACTIONS,
    ("fk", "release_waivers", "release_id"): _DUCK_FK_ACTIONS,
    ("fk", "research_assignments", "unit_id"): _DUCK_FK_ACTIONS,
    ("fk", "research_exchanges", "doc_id"): _DUCK_FK_ACTIONS,
    ("fk", "research_reviews", "assignment_id"): _DUCK_FK_ACTIONS,
    ("fk", "research_signals", "unit_id"): _DUCK_FK_ACTIONS,
    ("fk", "research_speakers", "doc_id"): _DUCK_FK_ACTIONS,
    ("fk", "research_units", "doc_id"): _DUCK_FK_ACTIONS,
    ("fk", "research_units", "speaker_id"): _DUCK_FK_ACTIONS,
    ("fk", "shadow_chunks", "doc_id"): _DUCK_FK_ACTIONS,
    ("fk", "shadow_chunks", "parent_id"): _DUCK_FK_ACTIONS,
    ("fk", "shadow_vectors", "shadow_chunk_id"): _DUCK_FK_ACTIONS,
    ("fk", "statement_conflicts", "statement_a_id"): _DUCK_FK_ACTIONS,
    ("fk", "statement_conflicts", "statement_b_id"): _DUCK_FK_ACTIONS,
    ("fk", "statements", "chunk_id"): _DUCK_FK_ACTIONS,
    ("fk", "statements", "document_id"): _DUCK_FK_ACTIONS,
    # -- CHECK constraints (W4 audit G8): the fold now compares CHECKs.
    # -- The spelling-only differences fold equal. The one real gap (duck
    # -- 035 omitted both meta_embeddings CHECKs) closed in duck 047.
}
