"""corpus-kb migrate-data: the Postgres <-> DuckDB swap (issue #127).

CLI-only by design (program §3.3): the server holds the DuckDB write
lock, so the swap runs while the stack is down. The command is a thin
adapter (zero-drift rule 1): it resolves direction and DSNs, calls the
``migrate_roundtrip.run_migration`` handler, and prints the report.
"""

from __future__ import annotations

import asyncio
import json

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
    """Attach the migrate-data command to the root CLI."""

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
