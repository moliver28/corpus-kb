"""The ``corpus-kb update`` orchestration (W5-a).

One handler for the operation (zero-drift rule 1); the CLI is a thin
adapter over :func:`run_update`. The flow, per the recorded W5-a
decision:

1. Gate the pip-family flavors on the PyPI probe (issue #463 honesty).
2. Build the per-flavor plan (``update_plan.build_plan``).
3. Check the store version FIRST — a store newer than the code refuses
   with the typed :class:`StoreNewerError` before anything applies.
4. ``--dry-run``: read the pending migrations through the shared runner
   (``dry_run=True``), report whether the store is current, apply
   nothing.
5. Real run: apply pending migrations through the shared runner, then
   re-check the store: schema verified = no code migration is missing.

An unreachable database is the typed ``DatabaseUnavailableError`` at
this boundary, so every surface answers one failure class per exit code.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

import asyncpg

from corpus_kb._setup.migrate import run_migrations
from corpus_kb._setup.migrate_report import MigrateReport
from corpus_kb._setup.update_flavor import InstallFlavor
from corpus_kb._setup.update_plan import UpdatePlan, build_plan, probe_pypi_published
from corpus_kb._setup.version_guard import (
    StoreVersionReport,
    assert_store_supported,
)
from corpus_kb.errors import DatabaseUnavailableError

MigrationRunner = Callable[..., Awaitable[MigrateReport]]
StoreChecker = Callable[[str, str], Awaitable[StoreVersionReport | None]]


@dataclass(frozen=True)
class UpdateResult:
    """What one update run saw and did, ready for the printers."""

    plan: UpdatePlan
    migration_report: MigrateReport | None
    schema_current: bool
    dry_run: bool

    def to_dict(self) -> dict[str, object]:
        """The ``--json`` payload."""
        return {
            "flavor": self.plan.flavor.value,
            "dry_run": self.dry_run,
            "schema_current": self.schema_current,
            "migrations": self.migration_report.to_dict() if self.migration_report else None,
            "plan": self.plan.to_dict(),
        }


def _backend_for(conn_str: str) -> str:
    """The engine a DSN names (the runner's own dispatch rule)."""
    return "duckdb" if conn_str.startswith("duckdb:") else "postgres"


async def run_update(
    conn_str: str,
    flavor: InstallFlavor,
    *,
    dry_run: bool = False,
    published_probe: Callable[[], bool | None] | None = None,
    migration_runner: MigrationRunner | None = None,
    store_checker: StoreChecker | None = None,
    compose_images: Sequence[str] = (),
) -> UpdateResult:
    """Plan the update, then apply and verify — or plan only on a dry run.

    The runner, checker, and probe seams default to the shared
    implementations; tests inject doubles to prove the execution
    honesty (``tests/test_update_run.py``).
    """
    checker = store_checker or assert_store_supported
    runner = migration_runner or run_migrations
    probe = published_probe or probe_pypi_published

    published: bool | None = True
    if flavor in (InstallFlavor.PIP, InstallFlavor.PIPX):
        published = probe()
    plan = build_plan(flavor, published=published, compose_images=compose_images)
    backend = _backend_for(conn_str)

    try:
        await checker(conn_str, backend)
        if dry_run:
            report = await runner(conn_str, dry_run=True)
            return UpdateResult(
                plan=plan,
                migration_report=report,
                schema_current=not report.pending,
                dry_run=True,
            )
        report = await runner(conn_str)
        after = await checker(conn_str, backend)
        schema_current = after is not None and after.pending_count == 0
        return UpdateResult(
            plan=plan,
            migration_report=report,
            schema_current=schema_current,
            dry_run=False,
        )
    except (ConnectionError, OSError, asyncpg.PostgresError) as exc:
        detail = str(exc) or type(exc).__name__
        raise DatabaseUnavailableError(
            f"Cannot reach the database to run the update: {detail}"
        ) from exc


__all__ = ["UpdateResult", "run_update"]
