"""The migration run report shared by both migrate backends.

``MigrateReport`` is the one type the DuckDB runner (``migrate.py``) and
the Postgres runner (``migrate_postgres.py``) both return. It lives in
its own import-free module so the two runners stay acyclic: the runners
import this module, and nothing here imports them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Migration file -> the server extension it needs (issue #508). Stock
#: Postgres lacks both; the docker-compose Postgres image ships both.
#: This map is the ONE owning definition; ``schema_apply`` re-exports the
#: name set for its readers.
GATED_MIGRATION_EXTENSIONS: dict[str, str] = {
    "004_enable_age.sql": "age",
    "005_enable_pgml.sql": "pgml",
}

#: The gated migration names (the set view of the map above).
EXTENSION_GATED_MIGRATIONS: frozenset[str] = frozenset(GATED_MIGRATION_EXTENSIONS)


@dataclass
class MigrateReport:
    """What one migrate run found and did (issue #348).

    ``applied`` names the migrations the tracking table held BEFORE the
    run, ``pending`` the ones that were outstanding before it, and
    ``applied_now`` the ones THIS run applied (empty on a dry run).
    ``skipped_extension_gated`` names the pending migrations the server
    cannot run because their extension is missing (issue #508). They are
    warned about and never recorded as applied.
    """

    backend: str
    applied: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    applied_now: list[str] = field(default_factory=list)
    dry_run: bool = False
    skipped_extension_gated: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """The ``--json`` payload: stable key order, plain lists."""
        return {
            "backend": self.backend,
            "applied": self.applied,
            "pending": self.pending,
            "applied_now": self.applied_now,
            "dry_run": self.dry_run,
            "skipped_extension_gated": self.skipped_extension_gated,
        }
