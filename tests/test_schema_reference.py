"""Reference-DDL contract pins for storage/schema.sql.

schema.sql is NOT purely documentation: tests/research_db.py executes it
verbatim (plus migrations 001+) against a scratch Postgres. These tests pin
the cross-file contracts so drift fails loudly in CI instead of at migration
time on a live database.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCHEMA_SQL = REPO / "src" / "corpus_kb" / "storage" / "schema.sql"
MIGRATIONS = REPO / "src" / "corpus_kb" / "migrations"


def test_chunks_vectors_embedding_is_the_halfvec_2560_contract() -> None:
    text = SCHEMA_SQL.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS chunks_vectors" in text, (
        "chunks_vectors table definition missing from schema.sql"
    )
    # The column line itself (not a comment) pins the typmod. halfvec is
    # REQUIRED at 2560 dims: pgvector HNSW caps `vector` at 2000, `halfvec`
    # at 4000 — a plain vector(2560) column could never carry its index.
    assert re.search(r"^\s+embedding halfvec\(2560\),\s*$", text, re.MULTILINE), (
        "chunks_vectors.embedding typmod drifted — it is the dimension-regime "
        "contract that migration 025 establishes; changing it requires a "
        "coordinated column migration"
    )
    # The typmod decision must stay documented in the file itself.
    assert "FIXED TYPODM" in text
    assert "migration 025" in text
    # The shipped embedding_model default tracks the default model.
    assert re.search(
        r"embedding_model VARCHAR\(255\) NOT NULL DEFAULT 'qwen3-embedding:4b'", text
    ), "chunks_vectors.embedding_model default drifted from the shipped model"


def test_chunks_vectors_carries_the_halfvec_hnsw_index() -> None:
    text = SCHEMA_SQL.read_text(encoding="utf-8")
    # No ivfflat CREATE INDEX on the main column: the 4096-dim attempts
    # are gone (the word may survive in comments documenting the drop).
    assert not re.search(r"^CREATE INDEX[^;]*ivfflat", text, re.MULTILINE), (
        "ivfflat index returned to schema.sql"
    )
    # The halfvec hnsw index mirrors retrieval.hnsw (m/ef_construction).
    assert re.search(
        r"USING hnsw \(embedding halfvec_cosine_ops\)\s*"
        r"WITH \(m = 16, ef_construction = 128\)",
        text,
    ), "schema.sql lost the migration-025 HNSW index shape"


def test_migration_012_creates_4096_and_migration_025_retipes() -> None:
    coding = (MIGRATIONS / "012_coding_schema.sql").read_text(encoding="utf-8")
    assert "vector(4096)" in coding, (
        "migration 012 lost its vector(4096) columns — history: they were the "
        "pre-regime shape (migration 025 retypes them to halfvec(2560))"
    )
    migration_025 = (MIGRATIONS / "025_halfvec_2560.sql").read_text(encoding="utf-8")
    for column in (
        "definition_vector",
        "probe_vector",
        "rationale_vector",
    ):
        assert column in migration_025, f"migration 025 lost the {column} retype"
    assert "answer_ctx_vectors" in migration_025
    assert "halfvec(2560)" in migration_025


def test_migration_025_renames_and_retipes_the_main_column() -> None:
    migration_025 = (MIGRATIONS / "025_halfvec_2560.sql").read_text(encoding="utf-8")
    assert "RENAME COLUMN vector TO embedding" in migration_025
    assert "ALTER COLUMN embedding TYPE halfvec(2560) USING NULL" in migration_025
    assert "halfvec_cosine_ops" in migration_025
    assert "ef_construction = 128" in migration_025
    # The rollback restores the 4096 shape (data cannot come back).
    rollback = (MIGRATIONS / "rollback_025_halfvec_2560.sql").read_text(encoding="utf-8")
    assert "ALTER COLUMN embedding TYPE vector(4096) USING NULL" in rollback
    assert "RENAME COLUMN embedding TO vector" in rollback


def test_research_grain_keeps_1024_halfvec_columns() -> None:
    research = (MIGRATIONS / "016_research_domain.sql").read_text(encoding="utf-8")
    # The research grain is the 1024-dim halfvec system (separate contract).
    assert "embedding vector(1024)" in research
    assert "halfvec_cosine_ops" in research


def test_shadow_grain_is_untouched_by_migration_025() -> None:
    migration_025 = (MIGRATIONS / "025_halfvec_2560.sql").read_text(encoding="utf-8")
    # DDL-level pin (comments may mention the untouched contracts).
    for table in ("shadow_vectors", "shadow_chunks", "research_units", "research_exchanges"):
        assert not re.search(
            rf"^\s*(ALTER TABLE|UPDATE|DROP TABLE|CREATE TABLE)\s+{table}",
            migration_025,
            re.MULTILINE,
        ), f"migration 025 must not touch {table} (separate contract)"
