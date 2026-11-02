"""The ``corpus-kb calibrate`` front door (issue #200).

Sub-app registered on the main Typer app:

  * ``corpus-kb calibrate apply PROFILE [--write] [--dry-run]`` — copy a
    profile lock's ``proposed_overrides`` into config.yaml.

The command is a thin adapter over the ONE shared handler
(``tools.calibrate_tools.calibrate_apply``); the MCP tool, the HTTP
route, and the socket arm call the same function. The default is a
preview: it prints the diff and writes nothing. ``--write`` applies.

HONEST EXIT CODES: ``0`` the op answered (a preview with no change is
still 0), ``1`` the op failed (unknown profile, no lock, stale lock,
unknown recipe keys), ``3`` no usable config.yaml file.
"""

from __future__ import annotations

import json

import typer

from corpus_kb.cli._plumbing import EXIT_FAILED, EXIT_OK
from corpus_kb.cli.cli_ingest import _run_sync

calibrate_app = typer.Typer(help="Apply calibration locks")


@calibrate_app.command("apply")
def apply(
    profile: str = typer.Argument(..., help="The profile whose calibration lock to apply"),
    write: bool = typer.Option(False, "--write", help="Write the changes into config.yaml"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the diff only. This is the default behavior."
    ),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Apply a profile lock's proposed overrides to config.yaml.

    Shows the diff first. Without --write nothing changes on disk. Exit
    codes: 0 answered, 1 failed, 3 no usable config.yaml file.
    """
    if write and dry_run:
        # Typer ignores plain int returns, so a non-zero exit must raise.
        from corpus_kb.errors import ValidationError

        exc = ValidationError(
            "--write and --dry-run cannot be used together.",
            remediation="Pass --write to apply, or pass neither flag to preview.",
        )
        print(f"ERROR: {exc}")
        print(f"  {exc.remediation}")
        raise typer.Exit(code=EXIT_FAILED)
    return _run_sync(lambda: _apply_flow(profile, write, json_output))


async def _apply_flow(profile: str, write: bool, json_output: bool) -> int:
    from corpus_kb.errors import CorpusError
    from corpus_kb.tools.calibrate_tools import calibrate_apply

    try:
        result = await calibrate_apply(profile, write=write)
    except CorpusError as exc:
        print(f"ERROR: {exc}")
        print(f"  {exc.remediation}")
        return exc.cli_exit
    _emit(result, json_output)
    return EXIT_OK if result.get("status") == "success" else EXIT_FAILED


def _emit(result: dict[str, object], json_output: bool) -> None:
    """Print the plain diff summary, or the exact envelope with --json."""
    if json_output:
        # The EXACT tool envelope — no reshaping, no added keys.
        print(json.dumps(result, indent=2, default=str))
        return
    inner = result.get("result")
    body = inner if isinstance(inner, dict) else {}
    mode = str(body.get("mode", "dry_run"))
    label = "write" if mode == "write" else "dry-run"
    print(f"profile: {body.get('profile')} (outcome: {body.get('outcome')})")
    print(f"mode: {label}")
    changes = body.get("changes")
    rows = list(changes) if isinstance(changes, list) else []
    for row in rows:
        mark = "" if row.get("changed") else "  (already applied)"
        print(f"  {row.get('key')}: {row.get('current')} -> {row.get('proposed')}{mark}")
    print(f"config: {body.get('config_path')}")
    applied = int(body.get("applied_count", 0) or 0)
    if mode == "write":
        print("no changes needed" if applied == 0 else f"{applied} change(s) written")
    else:
        pending = sum(1 for row in rows if row.get("changed"))
        print("nothing to apply" if pending == 0 else f"{pending} change(s) to apply (use --write)")
