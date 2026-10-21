"""``corpus-kb release ...`` CLI group (U5, P7, v6 §6).

Thin surface over ``handlers/release_handlers.py`` (the ONE shared layer —
this module parses flags, renders, and maps outcomes to honest exit codes;
it holds ZERO lifecycle logic). Registered in ``cli.py`` as the ``release``
group; the surface registry (``surface_registry.SURFACES``) and the
generated harness pack (``scripts/gen_harness_pack.py``) both point at
these commands.

The render/parse helpers and the release transition's body live in
:mod:`cli_release_support` (a pure move for the 250-line rule).

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

from corpus_kb.cli_release_support import (
    _finish,
    _print_assess,
    _print_review_packet,
    _print_status,
    _tenant,
    _uuid_or_raise,
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
    codebook_version_id: str = typer.Option(..., "--codebook-version-id", help="Version UUID"),
    profile: str = typer.Option(None, "--profile", help="Release profile (default from config)"),
    run_id: str = typer.Option(None, "--run-id", help="Research run UUID (reliability evidence)"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable payload"),
) -> int:
    """Compose every release gate over current evidence; WRITES NOTHING."""

    async def _run() -> int:
        import json

        from corpus_kb.cli_store import wired_store
        from corpus_kb.handlers import release_handlers

        async with wired_store() as wired:
            result = await release_handlers.assess_release(
                wired.store,
                _tenant(),
                _uuid_or_raise(codebook_version_id),
                profile_name=profile,
                run_id=_uuid_or_raise(run_id) if run_id else None,
            )
        payload = result.to_payload()
        if json_output:
            print(json.dumps(payload, indent=2, default=str))
        else:
            _print_assess(payload)
        return 0

    return _finish(_run())


# ---------------------------------------------------------------------------
# request
# ---------------------------------------------------------------------------


@release_app.command("request")
def release_request_cmd(
    codebook_version_id: str = typer.Option(..., "--codebook-version-id", help="Version UUID"),
    creator: str = typer.Option(..., "--creator", help="Who opens the draft"),
    codebook_id: str = typer.Option(None, "--codebook-id", help="Codebook UUID (default: version)"),
    profile: str = typer.Option(None, "--profile", help="Release profile (default from config)"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable payload"),
) -> int:
    """Open a draft_candidate release pinned to the current content hash."""

    async def _run() -> int:
        import json

        from corpus_kb.cli_store import wired_store
        from corpus_kb.handlers import release_handlers
        from corpus_kb.research import guide_copy

        async with wired_store() as wired:
            result = await release_handlers.request_release(
                wired.store,
                _tenant(),
                _uuid_or_raise(codebook_version_id),
                creator,
                codebook_id=_uuid_or_raise(codebook_id) if codebook_id else None,
                profile_name=profile,
            )
        payload = result.to_payload()
        if json_output:
            print(json.dumps(payload, indent=2, default=str))
        else:
            print(guide_copy.RELEASE_REQUEST_NOTE)
            print(
                f"requested {payload.get('release_id')} "
                f"(manifest {str(payload.get('manifest_sha256'))[:12]}..., "
                f"profile {payload.get('profile')})"
            )
            print(f"next: {guide_copy.RELEASE_NEXT_ASSESS}")
        return 0

    return _finish(_run())


# ---------------------------------------------------------------------------
# review (the packet)
# ---------------------------------------------------------------------------


@release_app.command("review")
def release_review_cmd(
    codebook_version_id: str = typer.Option(..., "--codebook-version-id", help="Version UUID"),
    release_id: str = typer.Option(..., "--release-id", help="Release UUID"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable payload"),
) -> int:
    """Render the review packet: evidence, failures, waivers, next actions."""

    async def _run() -> int:
        import json

        from corpus_kb.cli_store import wired_store
        from corpus_kb.handlers import release_handlers

        async with wired_store() as wired:
            result = await release_handlers.review_release(
                wired.store,
                _tenant(),
                _uuid_or_raise(codebook_version_id),
                _uuid_or_raise(release_id),
            )
        payload = result.to_payload()
        if json_output:
            print(json.dumps(payload, indent=2, default=str))
        else:
            _print_review_packet(payload)
        return 0

    return _finish(_run())


# ---------------------------------------------------------------------------
# release (the transition)
# ---------------------------------------------------------------------------


@release_app.command("release")
def release_execute_cmd(
    codebook_version_id: str = typer.Option(..., "--codebook-version-id", help="Version UUID"),
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
    codebook_version_id: str = typer.Option(..., "--codebook-version-id", help="Version UUID"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable payload"),
) -> int:
    """Active release, draft state, pending proposals, the next command."""

    async def _run() -> int:
        import json

        from corpus_kb.cli_store import wired_store
        from corpus_kb.handlers import release_handlers

        async with wired_store() as wired:
            result = await release_handlers.status_report(
                wired.store,
                _tenant(),
                _uuid_or_raise(codebook_version_id),
            )
        payload = result.to_payload()
        if json_output:
            print(json.dumps(payload, indent=2, default=str))
        else:
            _print_status(payload)
        return 0

    return _finish(_run())


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


@release_app.command("compare")
def release_compare_cmd(
    codebook_version_id: str = typer.Option(..., "--codebook-version-id", help="Version UUID"),
    old_release_id: str = typer.Option(..., "--old-release-id", help="Release A UUID (older)"),
    new_release_id: str = typer.Option(..., "--new-release-id", help="Release B UUID (newer)"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable payload"),
) -> int:
    """Diff two releases' manifests (codes, definitions, metrics, methods)."""
    return _finish(compare_run(codebook_version_id, old_release_id, new_release_id, json_output))
