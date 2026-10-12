"""``corpus-kb release ...`` CLI group (U5, P7, v6 §6).

Thin surface over ``handlers/release_handlers.py`` (the ONE shared layer —
this module parses flags, renders, and maps outcomes to honest exit codes;
it holds ZERO lifecycle logic). Registered in ``cli.py`` as the ``release``
group; the surface registry (``surface_registry.SURFACES``) and the
generated harness pack (``scripts/gen_harness_pack.py``) both point at
these commands.

Exit codes (D3, honest):
  * assess     — 0 whenever it ran: not_evaluable gates are REPORTED, never
                 a failure (assess writes nothing);
  * request    — 0 on the draft receipt;
  * review     — 0 (the packet is informational);
  * release    — 0 released, 1 BLOCKED (structured error on stdout), with
                 database/lifecycle errors mapped by cli._run_async;
  * status     — 0;
  * compare    — 0.

Every command takes ``--json`` (D4): the printed object is EXACTLY the
shared handler payload (stable schemas pinned by tests).
"""

from __future__ import annotations

from typing import cast
from uuid import UUID

import typer

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


def _finish(coro: object) -> int:
    """cli._run_async via a lazy import (avoids the cli <-> cli_release cycle)."""
    from corpus_kb.cli import _run_async

    return _run_async(coro)


def _uuid_or_raise(value: str) -> UUID:
    from uuid import UUID as _UUID

    return _UUID(value)


def _waiver_pairs(raw: list[str]) -> tuple[tuple[str, str], ...]:
    """Parse GATE=justification pairs; fail loud on a malformed entry."""
    pairs: list[tuple[str, str]] = []
    for entry in raw:
        gate, sep, justification = entry.partition("=")
        if not sep or not gate.strip() or not justification.strip():
            raise ValueError(f"--waive expects GATE_ID=justification, got {entry!r}")
        pairs.append((gate.strip(), justification.strip()))
    return tuple(pairs)


def _emit(payload: dict[str, object], json_output: bool) -> None:
    """--json prints the handler payload verbatim; human mode renders below."""
    import json

    if json_output:
        print(json.dumps(payload, indent=2, default=str))


async def _pool_conn():
    """One asyncpg pool per invocation (the cli.py house pattern)."""
    import asyncpg

    from corpus_kb.config import load_config

    cfg = load_config()
    db = cast("dict[str, object]", cfg.get("database") or {})
    return await asyncpg.create_pool(str(db.get("connection_string", "")))


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

        from corpus_kb.handlers import release_handlers
        from corpus_kb.research import guide_copy

        pool = await _pool_conn()
        try:
            result = await release_handlers.assess_release(
                pool,
                _tenant(),
                _uuid_or_raise(codebook_version_id),
                profile_name=profile,
                run_id=_uuid_or_raise(run_id) if run_id else None,
            )
        finally:
            await pool.close()
        payload = result.to_payload()
        if json_output:
            print(json.dumps(payload, indent=2, default=str))
        else:
            _print_assess(payload, guide_copy)
        return 0

    return _finish(_run())


def _print_assess(payload: dict[str, object], guide_copy: object) -> None:
    print(guide_copy.RELEASE_TITLE)
    print(guide_copy.RELEASE_ASSESS_HEADER)
    print(
        f"  version {payload.get('codebook_version_id')} "
        f"profile={payload.get('profile')} enforcement={payload.get('enforcement')}"
    )
    for gate in cast("list[dict[str, object]]", payload.get("gate_results") or []):
        print(
            "  "
            + guide_copy.RELEASE_LINE.format(
                gate_id=gate.get("gate_id"),
                status=gate.get("status"),
                reason=gate.get("reason"),
            )
        )
    sufficiency = cast("dict[str, bool]", payload.get("data_sufficiency") or {})
    print(f"\n{guide_copy.RELEASE_DATA_SUFFICIENCY_HEADER}")
    for block, present in sufficiency.items():
        mark = (
            guide_copy.RELEASE_SUFFICIENT_MARK if present else guide_copy.RELEASE_INSUFFICIENT_MARK
        )
        print(f"  {block}: {mark}")
    print(f"  ({guide_copy.RELEASE_INSUFFICIENT_NOTE})")
    print(f"\n{guide_copy.NEXT_ACTIONS_HEADER}")
    print(f"-> {guide_copy.RELEASE_NEXT_REVIEW}")


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

        from corpus_kb.handlers import release_handlers
        from corpus_kb.research import guide_copy

        pool = await _pool_conn()
        try:
            result = await release_handlers.request_release(
                pool,
                _tenant(),
                _uuid_or_raise(codebook_version_id),
                creator,
                codebook_id=_uuid_or_raise(codebook_id) if codebook_id else None,
                profile_name=profile,
            )
        finally:
            await pool.close()
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

        from corpus_kb.handlers import release_handlers
        from corpus_kb.research import guide_copy

        pool = await _pool_conn()
        try:
            result = await release_handlers.review_release(
                pool,
                _tenant(),
                _uuid_or_raise(codebook_version_id),
                _uuid_or_raise(release_id),
            )
        finally:
            await pool.close()
        payload = result.to_payload()
        if json_output:
            print(json.dumps(payload, indent=2, default=str))
        else:
            _print_review_packet(payload, guide_copy)
        return 0

    return _finish(_run())


def _print_review_packet(payload: dict[str, object], guide_copy: object) -> None:
    print(guide_copy.RELEASE_REVIEW_PACKET_HEADER)
    print(
        f"  release {payload.get('release_id')} state={payload.get('release_state')} "
        f"profile={payload.get('profile')} enforcement={payload.get('enforcement')}"
    )
    evidence = cast("list[dict[str, object]]", payload.get("evidence") or [])
    print(f"\n{guide_copy.RELEASE_EVIDENCE_HEADER}")
    if evidence:
        for gate in evidence:
            print(
                "  "
                + guide_copy.RELEASE_LINE.format(
                    gate_id=gate.get("gate_id"),
                    status=gate.get("status"),
                    reason=gate.get("reason"),
                )
            )
    else:
        print("  (no gate evaluated yet)")
    waived = cast("list[str]", payload.get("waived_gates") or [])
    print(f"\n{guide_copy.RELEASE_WAIVERS_HEADER}")
    for gate_id in waived:
        print(f"  {gate_id}")
    unmet = cast("list[str]", payload.get("unmet_required_gates") or [])
    if unmet:
        print(f"\n{guide_copy.RELEASE_UNMET_HEADER}")
        for gate_id in unmet:
            print(f"  {gate_id}")
    print(f"\n{guide_copy.NEXT_ACTIONS_HEADER}")
    for action in cast("list[str]", payload.get("next_actions") or []):
        print(f"-> {action}")


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

    async def _run() -> int:
        import json

        from corpus_kb.handlers import release_handlers

        pairs = _waiver_pairs(list(waive or []))
        pool = await _pool_conn()
        try:
            result = await release_handlers.execute_release(
                pool,
                _tenant(),
                _uuid_or_raise(codebook_version_id),
                _uuid_or_raise(release_id),
                approver,
                waivers=pairs,
                run_id=_uuid_or_raise(run_id) if run_id else None,
            )
        finally:
            await pool.close()
        payload = result.to_payload()
        if json_output or payload.get("status") == "blocked":
            print(json.dumps(payload, indent=2, default=str))
        else:
            print(
                f"released {payload.get('release_id')} "
                f"(approver {payload.get('approver')}, "
                f"enforcement {payload.get('enforcement')})"
            )
            for warning in cast("list[str]", payload.get("warnings") or []):
                print(f"WARNING: {warning}")
        if payload.get("status") == "blocked":
            return 1
        return 0

    return _finish(_run())


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

        from corpus_kb.handlers import release_handlers
        from corpus_kb.research import guide_copy

        pool = await _pool_conn()
        try:
            result = await release_handlers.status_report(
                pool,
                _tenant(),
                _uuid_or_raise(codebook_version_id),
            )
        finally:
            await pool.close()
        payload = result.to_payload()
        if json_output:
            print(json.dumps(payload, indent=2, default=str))
        else:
            _print_status(payload, guide_copy)
        return 0

    return _finish(_run())


def _print_status(payload: dict[str, object], guide_copy: object) -> None:
    stage = str(payload.get("current_stage", ""))
    labels = cast("dict[str, str]", guide_copy.RELEASE_STAGE_LABELS)
    print(guide_copy.RELEASE_TITLE)
    print(f"  version {payload.get('codebook_version_id')}")
    print(f"  stage: {stage} - {labels.get(stage, stage)}")
    print(f"  release_state: {payload.get('release_state')}")
    active = payload.get("active_release")
    if active:
        print(f"\n{guide_copy.RELEASE_ACTIVE_RELEASE_HEADER}")
        for key, value in cast("dict[str, object]", active).items():
            print(f"  {key}: {value}")
    draft = payload.get("draft")
    if draft:
        print(f"\n{guide_copy.RELEASE_DRAFT_HEADER}")
        for key, value in cast("dict[str, object]", draft).items():
            print(f"  {key}: {value}")
    blocking = cast("list[str]", payload.get("blocking_criteria") or [])
    if blocking:
        print(f"\n{guide_copy.RELEASE_UNMET_HEADER}")
        for criterion in blocking:
            print(f"  {criterion}")
    proposals = cast("list[dict[str, object]]", payload.get("pending_change_proposals") or [])
    print(f"\n{guide_copy.RELEASE_PROPOSALS_HEADER}: {len(proposals)}")
    for proposal in proposals:
        print(
            f"  successor {proposal.get('release_id')} of parent "
            f"{proposal.get('parent_release_id')}"
        )
    sufficiency = cast("dict[str, bool]", payload.get("data_sufficiency") or {})
    print(f"\n{guide_copy.RELEASE_DATA_SUFFICIENCY_HEADER}")
    for block, present in sufficiency.items():
        mark = (
            guide_copy.RELEASE_SUFFICIENT_MARK if present else guide_copy.RELEASE_INSUFFICIENT_MARK
        )
        print(f"  {block}: {mark}")
    if payload.get("release_state") == "none":
        print(f"\n({guide_copy.RELEASE_NO_RELEASE_NOTE})")
    print(f"\n{guide_copy.NEXT_ACTIONS_HEADER}")
    print(f"-> {payload.get('recommended_next_action')}")


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

    async def _run() -> int:
        import json

        from corpus_kb.handlers import release_handlers
        from corpus_kb.research import guide_copy

        result = await release_handlers.compare_version_releases(
            _tenant(),
            _uuid_or_raise(codebook_version_id),
            _uuid_or_raise(old_release_id),
            _uuid_or_raise(new_release_id),
        )
        payload = result.to_payload()
        if json_output:
            print(json.dumps(payload, indent=2, default=str))
        else:
            _print_compare(payload, guide_copy)
        return 0

    return _finish(_run())


def _print_compare(payload: dict[str, object], guide_copy: object) -> None:
    print(
        f"compare {payload.get('old_release_id')} -> {payload.get('new_release_id')}: "
        f"{'identical' if payload.get('identical') else 'DIFFERENT'}"
    )
    for key in (
        "codes_added",
        "codes_removed",
        "codes_merged",
        "definitions_changed",
        "method_changes",
        "threshold_changes",
        "metric_deltas",
    ):
        print(f"{key}: {payload.get(key)}")
    print(f"\nnext: {guide_copy.RELEASE_NEXT_STATUS}")


def _tenant():
    from corpus_kb.cli import _tenant as cli_tenant

    return cli_tenant()
