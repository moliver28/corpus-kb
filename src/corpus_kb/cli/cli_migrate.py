"""corpus-kb migrate and migrate-data (issues #348 and #127).

Both commands are CLI-only admin operations by design (program §3.3):
the server holds the DuckDB write lock and migrations are a stop-the-
world admin step, so no MCP/HTTP/socket surface exists for them. This
module is the recorded four-surface-rule EXEMPTION for ``migrate`` and
``migrate-data``: they are lifecycle admin commands like ``setup`` and
``quickstart``, not catalogue operations — one handler
(``_setup/migrate.py`` / ``handlers/migrate_roundtrip.py``), one CLI
surface (this module), no registry op.

Each command is a thin adapter (zero-drift rule 1): it resolves DSNs and
flags, calls the shared handler, and prints the report.
"""

from __future__ import annotations

import asyncio
import json
import os

import asyncpg
import typer

from corpus_kb._setup.migrate import DUCKDB_DSN_PREFIX
from corpus_kb.errors import ConfigError, CorpusError
from corpus_kb.storage.duckdb_support import is_duckdb_lock_error

_PG_SCHEMES = ("postgresql://", "postgres://")


def _resolve_dsns(source: str, to: str, conn_str: str) -> tuple[str, str, str]:
    """(direction, source dsn, target dsn) from --from/--to plus config."""
    if to.startswith(DUCKDB_DSN_PREFIX):
        effective_source = source or conn_str
        if effective_source.startswith(DUCKDB_DSN_PREFIX):
            raise ConfigError(
                "The source of a DuckDB swap is the Postgres database. Check database.backend."
            )
        return "postgres_to_duckdb", effective_source, to
    if to.startswith(_PG_SCHEMES):
        if not source.startswith(DUCKDB_DSN_PREFIX):
            raise ConfigError(
                "Name the DuckDB source with --from. Use the form duckdb:///C:/x.duckdb"
            )
        return "duckdb_to_postgres", source, to
    raise ConfigError("The --to value must be a duckdb:/// file or a postgresql:// database.")


def register(app: typer.Typer) -> None:
    """Attach the migration commands to the root CLI."""

    def _finish(code: int) -> int:
        """Honest exit: typer ignores plain int returns, so non-zero raises."""
        if code:
            raise typer.Exit(code=code)
        return 0

    @app.command(name="migrate")
    def migrate(
        dry_run: bool = typer.Option(
            False, "--dry-run", help="List the applied and pending migrations. Apply nothing."
        ),
        json_output: bool = typer.Option(
            False, "--json", help="Print the machine-readable report."
        ),
        superuser_dsn: str = typer.Option(
            "",
            "--superuser-dsn",
            help=(
                "Postgres DSN for steps that need a superuser. Ownership and "
                "grants go to the app role after the run. "
                "Default: CORPUS_KB_SUPERUSER_DSN."
            ),
        ),
        reset_embeddings: bool = typer.Option(
            False,
            "--reset-embeddings",
            help=(
                "Accept that migration 025 discards the embeddings stored in the old vector column."
            ),
        ),
        work_mem: str = typer.Option(
            "", "--work-mem", help="Session work_mem for heavy migrations. Example: 1GB."
        ),
        maintenance_work_mem: str = typer.Option(
            "",
            "--maintenance-work-mem",
            help="Session maintenance_work_mem for heavy migrations. Example: 1GB.",
        ),
    ) -> int:
        """Apply pending migrations to the configured database.

        Admin command (issue #348). Postgres and DuckDB both work. Prints
        the applied and pending migration names.
        """
        from corpus_kb._setup.migrate import run_migrations
        from corpus_kb._setup.migrate_admin import validate_session_settings
        from corpus_kb.config import load_config

        session_settings: dict[str, str] = {}
        if work_mem:
            session_settings["work_mem"] = work_mem
        if maintenance_work_mem:
            session_settings["maintenance_work_mem"] = maintenance_work_mem
        try:
            validate_session_settings(session_settings or None)
            cfg = load_config()
            conn_str = os.environ.get("CORPUS_KB_DATABASE_URL", "") or str(
                (cfg.get("database") or {}).get("connection_string", "") or ""
            )
            if not conn_str:
                raise ConfigError(
                    "No database found. Set database.connection_string in "
                    "config.yaml, or CORPUS_KB_DATABASE_URL."
                )
            su_dsn = superuser_dsn or os.environ.get("CORPUS_KB_SUPERUSER_DSN", "") or None
            report = asyncio.run(
                run_migrations(
                    conn_str,
                    superuser_dsn=su_dsn,
                    session_settings=session_settings or None,
                    reset_embeddings=reset_embeddings,
                    dry_run=dry_run,
                )
            )
        except CorpusError as exc:
            typer.echo(f"ERROR: {exc}")
            return _finish(exc.cli_exit)
        except (OSError, asyncpg.PostgresError) as exc:
            typer.echo(f"ERROR: Cannot reach the database. {exc}")
            return _finish(3)
        if json_output:
            typer.echo(json.dumps(report.to_dict(), indent=2))
            return 0
        typer.echo(f"Backend: {report.backend}")
        typer.echo(f"Already applied: {', '.join(report.applied) if report.applied else '(none)'}")
        typer.echo(f"Pending: {', '.join(report.pending) if report.pending else '(none)'}")
        if dry_run:
            typer.echo("Dry run. Nothing was applied.")
        elif report.applied_now:
            typer.echo(f"Applied now: {', '.join(report.applied_now)}")
        else:
            typer.echo("Nothing to apply. The database is up to date.")
        return 0

    @app.command(name="migrate-data")
    def migrate_data(
        to: str = typer.Option(
            ...,
            "--to",
            help="Target DSN: duckdb:///C:/data/x.duckdb or postgresql://...",
        ),
        source: str = typer.Option(
            "",
            "--from",
            help="Source DSN. Default: the configured database.",
        ),
    ) -> int:
        """Swap the corpus between Postgres and DuckDB. Stop the server first.

        Both Postgres DSNs need a role with BYPASSRLS (row level security
        would filter the copy).
        """
        from corpus_kb.config import load_config
        from corpus_kb.handlers.migrate_roundtrip import run_migration

        cfg = load_config()
        conn_str = str((cfg.get("database") or {}).get("connection_string", "") or "")
        if not source and not conn_str:
            raise ConfigError(
                "No source database found. Set CORPUS_KB_DATABASE_URL or pass --from."
            )
        direction, src, dst = _resolve_dsns(source, to, conn_str)
        try:
            report = asyncio.run(run_migration(direction, src, dst))
        except CorpusError as exc:
            typer.echo(f"ERROR: {exc}")
            return 1
        except Exception as exc:
            # The shared classifier owns the lock detection: raw driver
            # errors and gateway-typed ones classify the same way.
            if is_duckdb_lock_error(exc):
                typer.echo(
                    "ERROR: Another program is using this database file. Close it, then try again."
                )
                return 1
            raise
        typer.echo(json.dumps(report, indent=2, default=str))
        return 0
