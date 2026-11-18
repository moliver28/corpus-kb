"""APPEND_ONLY_TABLES stays the exact twin of the pg append-only triggers.

DuckDB has no triggers, so the gateway refuses UPDATE/DELETE on the
append-only tables through the ``APPEND_ONLY_TABLES`` frozenset
(``storage/duckdb_support.py``). Postgres enforces the same rule with
BEFORE UPDATE/DELETE triggers inside the migrations. The two lists drift
independently unless something pins them together: this module parses
every non-rollback pg migration for trigger-guarded tables and requires
set equality with the frozenset.

The pattern mirrors ``test_queue_tools``' migration-text coupling pins.
"""

from __future__ import annotations

import re
from pathlib import Path

from corpus_kb.storage.duckdb_support import APPEND_ONLY_TABLES

REPO = Path(__file__).resolve().parents[1]
MIGRATIONS = REPO / "src" / "corpus_kb" / "migrations"

_TRIGGER_EVENT_RE = re.compile(
    r"\bBEFORE\s+(UPDATE|DELETE)(?:\s+OR\s+(?:UPDATE|DELETE))?\s+ON\s+([A-Za-z_][A-Za-z_0-9]*)",
    re.IGNORECASE,
)


def _trigger_guarded_tables() -> set[str]:
    """Tables a numbered pg migration guards with BEFORE UPDATE/DELETE.

    Comment lines stay out: migration prose describes the rule in words
    that must not count as a trigger. Rollback files never create
    triggers, and the glob keeps them out anyway.
    """
    guarded: set[str] = set()
    for path in sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")):
        body = "\n".join(
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("--")
        )
        for match in _TRIGGER_EVENT_RE.finditer(body):
            guarded.add(match.group(2).lower())
    return guarded


def test_append_only_frozenset_matches_the_pg_triggers() -> None:
    """The DuckDB guard set equals the pg trigger-guarded table set, exactly."""
    guarded = _trigger_guarded_tables()
    assert guarded, "the trigger parse found nothing; the pattern is broken"
    assert set(APPEND_ONLY_TABLES) == guarded, (
        f"APPEND_ONLY_TABLES drift: duckdb-only={sorted(set(APPEND_ONLY_TABLES) - guarded)}, "
        f"pg-trigger-only={sorted(guarded - set(APPEND_ONLY_TABLES))}"
    )
