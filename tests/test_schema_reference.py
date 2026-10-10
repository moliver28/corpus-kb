"""Reference-DDL contract pins for storage/schema.sql.

schema.sql is NOT purely documentation: tests/research_db.py executes it
verbatim (plus migrations 001+) against a scratch Postgres, and migration
012 copies the chunks_vectors.vector typmod for answer_ctx_vectors. These
tests pin the cross-file contracts so drift fails loudly in CI instead of
at migration time on a live database.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCHEMA_SQL = REPO / "src" / "corpus_kb" / "storage" / "schema.sql"
MIGRATIONS = REPO / "src" / "corpus_kb" / "migrations"


def test_chunks_vectors_typmod_is_the_max_capacity_slot() -> None:
    text = SCHEMA_SQL.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS chunks_vectors" in text, (
        "chunks_vectors table definition missing from schema.sql"
    )
    # The column line itself (not a comment) pins the typmod.
    assert re.search(r"^\s+vector vector\(4096\),\s*$", text, re.MULTILINE), (
        "chunks_vectors.vector typmod drifted — it is the max-capacity slot that "
        "migration 012 (answer_ctx_vectors) and the 4096-dim fixtures align to; "
        "changing it requires a coordinated column migration"
    )
    # The typmod decision must stay documented in the file itself.
    assert "FIXED TYPODM" in text
    assert "migration 012" in text


def test_migration_012_declares_typmod_alignment_with_chunks_vectors() -> None:
    coding = (MIGRATIONS / "012_coding_schema.sql").read_text(encoding="utf-8")
    assert "vector(4096)" in coding, (
        "migration 012 lost its vector(4096) columns — it must match "
        "chunks_vectors.vector exactly (see schema.sql)"
    )


def test_research_grain_keeps_1024_halfvec_columns() -> None:
    research = (MIGRATIONS / "016_research_domain.sql").read_text(encoding="utf-8")
    # The research grain is the 1024-dim halfvec system (separate contract).
    assert "embedding vector(1024)" in research
    assert "halfvec_cosine_ops" in research
