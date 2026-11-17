"""corpus-kb update (W5-a self-update MVP).

Lifecycle admin command, recorded four-surface-rule EXEMPTION exactly
like ``migrate`` and ``setup`` (program §3.3): the update is a
stop-the-world database step, so no MCP/HTTP/socket surface exists.
One handler (``_setup/update_run.py``), one CLI surface (this module),
no registry op.

The command is a thin adapter (zero-drift rule 1): it detects the
install flavor, resolves the DSN, calls the shared handler, and prints
the plan or the report. Typed failures exit with the taxonomy's code.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import typer
import yaml

from corpus_kb._setup.update_flavor import InstallFlavor
from corpus_kb.errors import ConfigError, CorpusError

if TYPE_CHECKING:
    from corpus_kb._setup.update_run import UpdateResult


def _compose_images() -> list[str]:
    """The pinned image lines from a compose.yaml beside this directory."""
    path = Path("compose.yaml")
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return []
    services = (data or {}).get("services") or {}
    images: list[str] = []
    for spec in services.values():
        image = (spec or {}).get("image")
        if image:
            images.append(str(image))
    return images


def _resolve_conn_str() -> str:
    """The env DSN, else the configured one; refuse honestly with neither."""
    from corpus_kb.config import load_config

    conn_str = os.environ.get("CORPUS_KB_DATABASE_URL", "")
    if conn_str:
        return conn_str
    cfg = load_config()
    return str((cfg.get("database") or {}).get("connection_string", "") or "")


def register(app: typer.Typer) -> None:
    """Attach the update command to the root CLI."""

    @app.command(name="update")
    def update(
        dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan. Change nothing."),
        json_output: bool = typer.Option(
            False, "--json", help="Print the machine-readable report."
        ),
    ) -> int:
        """Update Corpus-KB: plan the upgrade, apply migrations, verify the store.

        Detects how this install was put on the machine, prints the
        upgrade steps for that flavor, then applies pending migrations
        and verifies the store against the code. With --dry-run it only
        prints the plan and the pending migration facts.
        """
        from corpus_kb._setup.update_flavor import detect_flavor
        from corpus_kb._setup.update_run import run_update

        flavor = detect_flavor()
        try:
            conn_str = _resolve_conn_str()
            if not conn_str:
                raise ConfigError(
                    "No database found. Set database.connection_string in "
                    "config.yaml, or CORPUS_KB_DATABASE_URL."
                )
            result = asyncio.run(
                run_update(
                    conn_str,
                    flavor,
                    dry_run=dry_run,
                    compose_images=_compose_images() if flavor is InstallFlavor.COMPOSE else (),
                )
            )
        except CorpusError as exc:
            typer.echo(f"ERROR: {exc}")
            raise typer.Exit(code=exc.cli_exit) from None
        if json_output:
            typer.echo(json.dumps(result.to_dict(), indent=2))
            return 0
        _print_report(result, dry_run)
        return 0


def _print_report(result: UpdateResult, dry_run: bool) -> None:
    """The human report: flavor, steps, facts, restart guidance."""
    typer.echo(f"Flavor: {result.plan.flavor.value}")
    for index, step in enumerate(result.plan.upgrade_steps, start=1):
        typer.echo(f"  {index}. {step.label}: {step.command}")
    for note in result.plan.notes:
        typer.echo(f"Note: {note}")
    report = result.migration_report
    if report is not None:
        if report.pending:
            typer.echo(f"Migrations: {len(report.pending)} pending.")
        else:
            typer.echo("Migrations: the store has every migration.")
        if report.applied_now:
            typer.echo(f"Applied now: {', '.join(report.applied_now)}")
    typer.echo(
        "Store schema: current."
        if result.schema_current
        else "Store schema: behind the code. Run the update to apply migrations."
    )
    typer.echo(f"Restart: {result.plan.restart}")
    if dry_run:
        typer.echo("Dry run. Nothing was applied.")


__all__ = ["register"]
