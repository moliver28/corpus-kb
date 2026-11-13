"""The migration run report shared by both migrate backends.

``MigrateReport`` is the one type the DuckDB runner (``migrate.py``) and
the Postgres runner (``migrate_postgres.py``) both return. It lives in
its own import-free module so the two runners stay acyclic: the runners
import this module, and nothing here imports them.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MigrateReport:
    """What one migrate run found and did (issue #348).

    ``applied`` names the migrations the tracking table held BEFORE the
    run, ``pending`` the ones that were outstanding before it, and
    ``applied_now`` the ones THIS run applied (empty on a dry run).
    """

    backend: str
    applied: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    applied_now: list[str] = field(default_factory=list)
    dry_run: bool = False

    def to_dict(self) -> dict[str, object]:
        """The ``--json`` payload: stable key order, plain lists."""
        return {
            "backend": self.backend,
            "applied": self.applied,
            "pending": self.pending,
            "applied_now": self.applied_now,
            "dry_run": self.dry_run,
        }
