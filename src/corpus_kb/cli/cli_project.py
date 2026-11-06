"""Project-table op CLI commands (Track C2).

Three op commands over the shared handlers, mirroring cli_queue's pool
lifecycle but calling the handlers directly (their required ``slug``
sits before any defaulted parameter, so the ``flow`` config-injection
shape does not apply). Imports corpus_kb.cli nowhere, so the import in
cli/__init__ stays acyclic.
"""

from __future__ import annotations

import json

import typer

from corpus_kb.cli._plumbing import _JSON_OPT, _TENANT_OPT, EXIT_FAILED, EXIT_OK


def register(app: typer.Typer) -> None:
    """Attach the three project commands to the main Typer app."""
    app.command("project-provision")(project_provision)
    app.command("project-list")(project_list)
    app.command("project-drop")(project_drop)


def _emit(result: dict[str, object], json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, indent=2, default=str))
        return
    print(f"project: {result.get('status', 'error')}")
    for key in ("table", "registry_id", "backend", "already_provisioned", "dropped_rows", "count"):
        if key in result:
            print(f"  {key}: {result[key]}")
    entries = result.get("entries")
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict):
                print(f"  - {entry.get('table')} rows={entry.get('row_count')}")


async def _run(op_name: str, kwargs: dict[str, object], json_output: bool) -> int:
    """Open the pool (or gateway), call the shared handler, emit, close."""
    from corpus_kb.cli import cli_ingest
    from corpus_kb.errors import CorpusError
    from corpus_kb.tools import project_tools

    cfg = cli_ingest._load_config()
    pool = await cli_ingest._open_pool(cfg)
    try:
        handler = getattr(project_tools, op_name)
        result = await handler(pool, **kwargs)
    except CorpusError as exc:
        print(f"ERROR: {exc}")
        if exc.remediation:
            print(f"  {exc.remediation}")
        return EXIT_FAILED
    finally:
        await pool.close()
    _emit(result, json_output)
    return EXIT_OK


def _dispatch(op_name: str, kwargs: dict[str, object], json_output: bool) -> int:
    from corpus_kb.cli.cli_ingest import _run_sync

    return _run_sync(lambda: _run(op_name, dict(kwargs), json_output))


def project_provision(
    slug: str = typer.Argument(..., help="The project slug, like meeting-notes"),
    project_id: str = typer.Option(None, "--project-id", help="Research project id"),
    tenant_id: str = _TENANT_OPT,
    json_output: bool = _JSON_OPT,
) -> int:
    """Create the raw table for one project slug (idempotent)."""
    kwargs: dict[str, object] = {
        "slug": slug,
        "project_id": project_id,
        "tenant_id": tenant_id,
    }
    return _dispatch("project_provision", kwargs, json_output)


def project_list(
    tenant_id: str = _TENANT_OPT,
    json_output: bool = _JSON_OPT,
) -> int:
    """List the provisioned project raw tables with row counts."""
    kwargs: dict[str, object] = {"tenant_id": tenant_id}
    return _dispatch("project_list", kwargs, json_output)


def project_drop(
    slug: str = typer.Argument(..., help="The project slug to drop"),
    tenant_id: str = _TENANT_OPT,
    json_output: bool = _JSON_OPT,
) -> int:
    """Drop one project raw table and its registry row."""
    kwargs: dict[str, object] = {"slug": slug, "tenant_id": tenant_id}
    return _dispatch("project_drop", kwargs, json_output)
