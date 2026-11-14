"""``corpus-kb release ...`` CLI group (U5, P7, v6 §6).

Thin surface over ``handlers/release_handlers.py`` (the ONE shared layer —
this module parses flags and maps outcomes to honest exit codes;
it holds ZERO lifecycle logic). Registered in ``cli.py`` as the ``release``
group; the surface registry (``surface_registry.SURFACES``) and the
generated harness pack (``scripts/gen_harness_pack.py``) both point at
these commands.

The render/parse helpers and the release transition's body live in
:mod:`cli_release_support`; the assess/request/review/status bodies live
in :mod:`cli_release_flows` (pure moves for the 250-line rule).

Exit codes (D3, honest): assess 0 whenever it ran (not_evaluable gates
are REPORTED, never a failure — assess writes nothing); request 0 on the
draft receipt; review 0 (the packet is informational); release 0 released
/ 1 BLOCKED (structured error on stdout, database/lifecycle errors mapped
by cli._run_async); status 0; compare 0.

Every command takes ``--json`` (D4): the printed object is EXACTLY the
shared handler payload (stable schemas pinned by tests).
"""

from __future__ import annotations

import typer

from corpus_kb.cli.cli_release_flows import (
    assess_run,
    request_run,
    review_run,
    status_run,
)
from corpus_kb.cli.cli_release_support import (
    _finish,
    _waiver_pairs,
    compare_run,
    execute_run,
)

# Test-visible re-export: tests/test_release_cli.py imports _waiver_pairs
# from THIS module (the pre-split import path).
__all__ = ["_waiver_pairs", "release_app"]

release_app = typer.Typer(
    help="Release lifecycle (v6 §6): assess, request, review, release, status, compare"
)

# Module-level singleton (B008): mutable-annotated typer defaults must not
# call typer.Option inline (cli.py house pattern).
_WAIVE_OPTION = typer.Option(
    None,
    "--waive",
    help="Waive one required gate: --waive GATE_ID=justification (repeatable)",
)


# ---------------------------------------------------------------------------
# assess
# ---------------------------------------------------------------------------
@release_app.command("assess")
def release_assess_cmd(
    codebook_version_id: str = typer.Option(
        ...,
        "--codebook-version-id",
        help="Version UUID. corpus-kb codebook list prints the versions",
    ),
    profile: str = typer.Option(None, "--profile", help="Release profile (default from config)"),
    run_id: str = typer.Option(None, "--run-id", help="Research run UUID (reliability evidence)"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable payload"),
) -> int:
    """Compose every release gate over current evidence; WRITES NOTHING."""
    return _finish(assess_run(codebook_version_id, profile, run_id, json_output))


# ---------------------------------------------------------------------------
# request
# ---------------------------------------------------------------------------


@release_app.command("request")
def release_request_cmd(
    codebook_version_id: str = typer.Option(
        ...,
        "--codebook-version-id",
        help="Version UUID. corpus-kb codebook list prints the versions",
    ),
    creator: str = typer.Option(..., "--creator", help="Who opens the draft"),
    codebook_id: str = typer.Option(None, "--codebook-id", help="Codebook UUID (default: version)"),
    profile: str = typer.Option(None, "--profile", help="Release profile (default from config)"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable payload"),
) -> int:
    """Open a draft_candidate release pinned to the current content hash."""
    return _finish(request_run(codebook_version_id, creator, codebook_id, profile, json_output))


# ---------------------------------------------------------------------------
# review (the packet)
# ---------------------------------------------------------------------------


@release_app.command("review")
def release_review_cmd(
    codebook_version_id: str = typer.Option(
        ...,
        "--codebook-version-id",
        help="Version UUID. corpus-kb codebook list prints the versions",
    ),
    release_id: str = typer.Option(..., "--release-id", help="Release UUID"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable payload"),
) -> int:
    """Render the review packet: evidence, failures, waivers, next actions."""
    return _finish(review_run(codebook_version_id, release_id, json_output))


# ---------------------------------------------------------------------------
# release (the transition)
# ---------------------------------------------------------------------------


@release_app.command("release")
def release_execute_cmd(
    codebook_version_id: str = typer.Option(
        ...,
        "--codebook-version-id",
        help="Version UUID. corpus-kb codebook list prints the versions",
    ),
    release_id: str = typer.Option(..., "--release-id", help="Draft release UUID"),
    approver: str = typer.Option(..., "--approver", help="Human approver (required)"),
    waive: list[str] = _WAIVE_OPTION,
    run_id: str = typer.Option(None, "--run-id", help="Research run UUID (reliability evidence)"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable payload"),
) -> int:
    """Evaluate gates, record results, apply waivers, execute the release."""
    return _finish(
        execute_run(
            codebook_version_id, release_id, approver, list(waive or []), run_id, json_output
        )
    )


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@release_app.command("status")
def release_status_cmd(
    codebook_version_id: str = typer.Option(
        ...,
        "--codebook-version-id",
        help="Version UUID. corpus-kb codebook list prints the versions",
    ),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable payload"),
) -> int:
    """Active release, draft state, pending proposals, the next command."""
    return _finish(status_run(codebook_version_id, json_output))


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


@release_app.command("compare")
def release_compare_cmd(
    codebook_version_id: str = typer.Option(
        ...,
        "--codebook-version-id",
        help="Version UUID. corpus-kb codebook list prints the versions",
    ),
    old_release_id: str = typer.Option(..., "--old-release-id", help="Release A UUID (older)"),
    new_release_id: str = typer.Option(..., "--new-release-id", help="Release B UUID (newer)"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable payload"),
) -> int:
    """Diff two releases' manifests (codes, definitions, metrics, methods)."""
    return _finish(compare_run(codebook_version_id, old_release_id, new_release_id, json_output))
