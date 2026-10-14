"""Corpus-KB user-facing CLI.

Wires the setup/doctor diagnostics and server start commands behind a single
``corpus-kb`` entry point.
"""

from __future__ import annotations

import typer

# Zero-drift Wave 3B split: the research/coding/codebook commands live in
# cli_research* / cli_coding (the 250-line limit per file), the shared CLI
# plumbing in cli_support (wired_pool, _run_async, the approval boundary,
# the canonical tenant id from contracts.model). None of them imports
# corpus_kb.cli, so these imports stay acyclic. cli.py re-exports the moved
# helper names so tests and cli_release keep their import paths.
from corpus_kb.cli_coding import codebook_app, coding_app

# Stage-5 ingest front door (top-level corpus-kb ingest / ingest-status /
# ingest-reembed): the commands live in cli_ingest.py, which imports
# corpus_kb.cli only lazily (inside functions), so this import is acyclic.
from corpus_kb.cli_ingest import register as register_ingest_commands

# Operation inventory (Wave 2 zero-drift): `corpus-kb operations` emits the
# machine-readable catalogue (contracts/) — surfaces, params, envelope keys,
# error map — plus, with --openapi, the generated HTTP spec. cli_operations
# never imports corpus_kb.cli (it registers via the function below), so this
# import is acyclic.
from corpus_kb.cli_operations import register as register_operations

# Release lifecycle group (U5, P7): the commands live in cli_release.py (the
# 250-line soft limit; cli.py is already the largest surface file) and share
# the handlers/release_handlers.py layer with the MCP + HTTP surfaces.
# cli_release imports corpus_kb.cli only lazily (inside functions), so this
# top-level import is acyclic.
from corpus_kb.cli_release import release_app
from corpus_kb.cli_research import research_app
from corpus_kb.cli_research_ingest import _print_detection_summary, _print_override_notice
from corpus_kb.cli_research_report import _print_report
from corpus_kb.cli_support import (
    DEFAULT_TENANT,
    _cli_approve,
    _cli_error_exit,
    _pid,
    _run_async,
    _tenant,
    cast_dict,
    cast_list,
    wired_pool,
)

# Backward-compatible alias: cli.py owned the tenant constant as a private
# module global before the Wave 3B split.
_DEFAULT_TENANT = DEFAULT_TENANT

app = typer.Typer(help="Corpus-KB command-line interface")

register_ingest_commands(app)
register_operations(app)


def _version_callback(value: bool) -> None:
    if value:
        from corpus_kb import __version__

        typer.echo(__version__)
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    transport: str = typer.Option(
        None,
        "--transport",
        help="Serve the MCP tools over stdio (editor agents). HTTP: corpus-kb start",
    ),
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Print the version and exit",
    ),
) -> None:
    """Corpus-KB: local RAG knowledge base for AI code editors."""
    if transport is not None:
        if transport != "stdio":
            # The root --transport flag serves ONE purpose: the mcp-configs/
            # editor configs launch `corpus-kb --transport stdio`. Anything
            # else gets an honest pointer; the HTTP server is `start`.
            typer.echo(
                f"Unknown transport '{transport}' for the root command. "
                "Use `corpus-kb --transport stdio` (editor agents) or "
                "`corpus-kb start --transport http`."
            )
            raise typer.Exit(code=2)
        # The editor-facing stdio surface: serve the registry-generated MCP
        # tools until the editor disconnects (blocks; exit code is the
        # taxonomy's: 0 clean, 3 config/database).
        from corpus_kb.mcp_server import run_stdio_server

        raise typer.Exit(code=run_stdio_server())
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


@app.command()
def setup(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print steps without executing"),
    fresh: bool = typer.Option(False, "--fresh", help="Reset checkpoint and start from phase 1"),
    build_local: bool = typer.Option(
        False,
        "--build-local",
        help=(
            "Build the Postgres image from docker/postgres/Dockerfile "
            "instead of using the pre-built image"
        ),
    ),
    skip_image_verify: bool = typer.Option(
        False,
        "--skip-image-verify",
        help="Skip cosign verification of the Postgres container image",
    ),
) -> int:
    """One-line docker-compose + database + migrations + models setup."""
    from corpus_kb._setup.install import main as install_main

    argv = ["setup"]
    if dry_run:
        argv.append("--dry-run")
    if fresh:
        argv.append("--fresh")
    if build_local:
        argv.append("--build-local")
    if skip_image_verify:
        argv.append("--skip-image-verify")
    return install_main(argv)


@app.command()
def doctor() -> int:
    """Read-only system diagnostics."""
    from corpus_kb._setup.install import main as install_main

    return install_main(["doctor"])


@app.command()
def start(
    transport: str = typer.Option("http", "--transport", help="Server transport"),
    port: int = typer.Option(8010, "--port", help="HTTP server port"),
) -> int:
    """Start the Corpus-KB server."""
    from corpus_kb.server_wiring import main as server_main

    server_main(["--transport", transport, "--port", str(port)])
    return 0


# Sub-app attachment order IS the root help order (help-diff gate):
# research, coding, codebook, review, release.
app.add_typer(research_app, name="research")
app.add_typer(coding_app, name="coding")
app.add_typer(codebook_app, name="codebook")

review_app = typer.Typer(help="Review queue: execute accept/override decisions")
app.add_typer(review_app, name="review")

app.add_typer(release_app, name="release")


def _review_command(decision: str):
    def _cmd(
        assignment_id: str = typer.Argument(..., help="CodingAssignment aggregate UUID"),
        reviewer: str = typer.Option(..., "--reviewer", help="Reviewer identity"),
        note: str = typer.Option(
            "", "--note", help="Decision note (link fixes describe the change)"
        ),
    ) -> int:
        """Record one reviewer decision and advance the projections."""
        import json

        async def _run() -> int:
            from uuid import UUID

            from corpus_kb.research.review_surface import execute_review

            async with wired_pool() as wired:
                result = await execute_review(
                    wired.pool,
                    _tenant(),
                    wired.conn_str,
                    UUID(assignment_id),
                    reviewer,
                    decision,
                    note,
                )
                print(json.dumps(result, indent=2, default=str))
                return 0

        return _run_async(_run())

    return _cmd


review_app.command("accept", help="Confirm the model's assignment")(_review_command("accept"))
review_app.command("override", help="Overrule the model's assignment")(_review_command("override"))

# Moved-name re-exports (zero-drift Wave 3B): the cli_support helpers, the
# detection/override printers (cli_support) and _print_report
# (cli_research_report) keep their historical import paths — tests and
# cli_release import them from corpus_kb.cli.
__all__ = [
    "DEFAULT_TENANT",
    "_DEFAULT_TENANT",
    "_cli_approve",
    "_cli_error_exit",
    "_pid",
    "_print_detection_summary",
    "_print_override_notice",
    "_print_report",
    "_run_async",
    "_tenant",
    "app",
    "cast_dict",
    "cast_list",
    "codebook_app",
    "coding_app",
    "release_app",
    "research_app",
    "review_app",
    "wired_pool",
]
