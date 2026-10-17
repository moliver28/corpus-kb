"""The ``corpus-kb quickstart`` front door (novice first-run path).

One command from a fresh clone to a first successful search: probe
Ollama, resolve a reachable Postgres DSN (configured, then ``--dsn``,
then the localhost:5432 twin, then provisioning via ``--superuser-dsn``),
apply the schema, write the adopted DSN back to the local config.yaml,
check the Ollama models, and close with the doctor report.

A lifecycle command in the setup/doctor family — NOT a catalogue
operation, so ``surface_registry`` and the parity gates are untouched.

HONEST EXIT CODES: ``0`` ready, ``1`` a step failed, ``3`` config error.
Every step is PRINTED as ``[STATE] name: detail`` or emitted as
``--json`` — no silent steps.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess

import httpx
import typer

from corpus_kb._setup.config_writeback import config_write_target, write_connection_string
from corpus_kb._setup.db_provision import ensure_role_and_database, probe_dsn
from corpus_kb._setup.quickstart import QuickstartRunner
from corpus_kb._setup.quickstart_types import (
    QuickstartDeps,
    QuickstartOptions,
    QuickstartOutcome,
)
from corpus_kb._setup.schema_apply import SchemaApplyResult, apply_schema

EXIT_CONFIG = 3


def register(app: typer.Typer) -> None:
    """Attach the quickstart command to the main Typer app."""
    app.command("quickstart")(quickstart)


async def _ollama_get(base_url: str, path: str) -> tuple[int, dict[str, object]] | None:
    """GET one Ollama endpoint; None when the server does not answer."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{base_url}{path}")
            return response.status_code, response.json()
    except Exception:
        return None


def _pull_model(model: str) -> int:
    """Run ``ollama pull`` in the foreground (output streams to the user)."""
    result = subprocess.run(["ollama", "pull", model], check=False)
    return result.returncode


async def _run_doctor(config: dict[str, object]) -> int:
    """The doctor entry path, run programmatically on the adopted config."""
    from corpus_kb._setup.install import doctor_cmd

    return await doctor_cmd(config)


async def _apply_schema(dsn: str) -> SchemaApplyResult:
    """Apply storage/schema.sql plus migrations (the shared implementation)."""
    return await apply_schema(dsn)


def _set_env_dsn(dsn: str) -> None:
    """Pin the adopted DSN for this process so doctor probes the same one."""
    os.environ["CORPUS_KB_DATABASE_URL"] = dsn


def build_deps(config: dict[str, object]) -> QuickstartDeps:
    """Real collaborators (tests monkeypatch this whole factory)."""
    return QuickstartDeps(
        config=config,
        ollama_get=_ollama_get,
        pg_probe=probe_dsn,
        docker_available=lambda: shutil.which("docker") is not None,
        provision=ensure_role_and_database,
        apply_schema=_apply_schema,
        write_dsn=lambda dsn: write_connection_string(config_write_target(), dsn),
        pull_model=_pull_model,
        run_doctor=_run_doctor,
        set_env_dsn=_set_env_dsn,
    )


def _print_report(outcome: QuickstartOutcome, json_output: bool, config_error: str) -> None:
    """Print the step lines, or the JSON summary when ``--json``."""
    code = outcome.exit_code
    if json_output:
        summary: dict[str, object] = {
            "status": "config_error" if config_error else ("ok" if code == 0 else "failed"),
            "exit_code": 3 if config_error else code,
            "adopted_dsn": outcome.adopted_dsn,
            "steps": [
                {"name": step.name, "state": step.state, "detail": step.detail}
                for step in outcome.steps
            ],
        }
        if config_error:
            summary["config_error"] = config_error
        print(json.dumps(summary, indent=2))
        return
    print("\n=== Corpus-KB Quickstart ===")
    for step in outcome.steps:
        print(f"[{step.state}] {step.name}: {step.detail}")
        for line in step.extra:
            print(f"  {line}")
    if config_error:
        print(f"[FAIL] config: {config_error}")
    elif code:
        print("Quickstart failed. Fix the FAIL lines above, then re-run corpus-kb quickstart.")
    print("Next steps:")
    print("  corpus-kb ingest docs")
    print('  corpus-kb search "your question here"')
    print("  corpus-kb quickstart --help")
    print("===========================\n")


def quickstart(
    dsn: str = typer.Option(None, "--dsn", help="Adopt this Postgres DSN for this run"),
    superuser_dsn: str = typer.Option(
        None,
        "--superuser-dsn",
        help="Superuser DSN used to create a missing role and database",
    ),
    no_write: bool = typer.Option(
        False,
        "--no-write",
        help="Do not write the adopted DSN into config.yaml",
    ),
    pull_models: bool = typer.Option(
        True,
        "--pull-models/--no-pull-models",
        help="Pull missing Ollama models during the run",
    ),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON summary"),
) -> int:
    """Take a fresh clone to a first successful search.

    Probes Ollama, resolves Postgres, applies the schema, and checks
    the models.

    Exit codes: 0 ready, 1 a step failed, 3 config error.
    """
    from corpus_kb.config import load_config

    opts = QuickstartOptions(
        dsn=dsn,
        superuser_dsn=superuser_dsn,
        no_write=no_write,
        pull_models=pull_models,
        json_output=json_output,
    )
    runner: QuickstartRunner | None = None
    try:
        config = load_config()
        runner = QuickstartRunner(opts, build_deps(config))
        code = asyncio.run(runner.run())
    except ValueError as exc:
        outcome = runner.outcome if runner is not None else QuickstartOutcome()
        _print_report(outcome, json_output, config_error=str(exc))
        raise typer.Exit(code=EXIT_CONFIG) from None
    _print_report(runner.outcome, json_output, config_error="")
    if code:
        raise typer.Exit(code=code)
    return 0


__all__ = ["build_deps", "quickstart", "register"]
