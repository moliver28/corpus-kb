"""Release CLI support helpers (pure move out of ``cli_release.py``).

The ``--json``/human printers, the small parse helpers, and the release
transition's wired-store body behind ``corpus-kb release ...`` (250-line
rule). Byte-identical bodies; ``cli_release`` re-imports them so its
commands and the test imports keep working.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID


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


def _emit(payload: dict[str, Any], json_output: bool) -> None:
    """--json prints the handler payload verbatim; human mode renders below."""
    import json

    if json_output:
        print(json.dumps(payload, indent=2, default=str))


def _print_assess(payload: dict[str, Any]) -> None:
    from corpus_kb.research import guide_copy

    print(guide_copy.RELEASE_TITLE)
    print(guide_copy.RELEASE_ASSESS_HEADER)
    print(
        f"  version {payload.get('codebook_version_id')} "
        f"profile={payload.get('profile')} enforcement={payload.get('enforcement')}"
    )
    for gate in cast("list[dict[str, Any]]", payload.get("gate_results") or []):
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


def _print_review_packet(payload: dict[str, Any]) -> None:
    from corpus_kb.research import guide_copy

    print(guide_copy.RELEASE_REVIEW_PACKET_HEADER)
    print(
        f"  release {payload.get('release_id')} state={payload.get('release_state')} "
        f"profile={payload.get('profile')} enforcement={payload.get('enforcement')}"
    )
    evidence = cast("list[dict[str, Any]]", payload.get("evidence") or [])
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


def _print_status(payload: dict[str, Any]) -> None:
    from corpus_kb.research import guide_copy

    stage = str(payload.get("current_stage", ""))
    labels = cast("dict[str, str]", guide_copy.RELEASE_STAGE_LABELS)
    print(guide_copy.RELEASE_TITLE)
    print(f"  version {payload.get('codebook_version_id')}")
    print(f"  stage: {stage} - {labels.get(stage, stage)}")
    print(f"  release_state: {payload.get('release_state')}")
    active = payload.get("active_release")
    if active:
        print(f"\n{guide_copy.RELEASE_ACTIVE_RELEASE_HEADER}")
        for key, value in cast("dict[str, Any]", active).items():
            print(f"  {key}: {value}")
    draft = payload.get("draft")
    if draft:
        print(f"\n{guide_copy.RELEASE_DRAFT_HEADER}")
        for key, value in cast("dict[str, Any]", draft).items():
            print(f"  {key}: {value}")
    blocking = cast("list[str]", payload.get("blocking_criteria") or [])
    if blocking:
        print(f"\n{guide_copy.RELEASE_UNMET_HEADER}")
        for criterion in blocking:
            print(f"  {criterion}")
    proposals = cast("list[dict[str, Any]]", payload.get("pending_change_proposals") or [])
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


def _print_compare(payload: dict[str, Any]) -> None:
    from corpus_kb.research import guide_copy

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


async def execute_run(
    codebook_version_id: str,
    release_id: str,
    approver: str,
    waive: list[str],
    run_id: str | None,
    json_output: bool,
) -> int:
    """The release transition's body (pure move from cli_release.execute)."""
    import json

    from corpus_kb.cli_store import wired_store
    from corpus_kb.handlers import release_handlers

    pairs = _waiver_pairs(list(waive or []))
    async with wired_store() as wired:
        result = await release_handlers.execute_release(
            wired.store,
            _tenant(),
            _uuid_or_raise(codebook_version_id),
            _uuid_or_raise(release_id),
            approver,
            waivers=pairs,
            run_id=_uuid_or_raise(run_id) if run_id else None,
        )
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


async def compare_run(
    codebook_version_id: str, old_release_id: str, new_release_id: str, json_output: bool
) -> int:
    """The compare command's body (pure move from cli_release.compare)."""
    import json

    from corpus_kb.handlers import release_handlers

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
        _print_compare(payload)
    return 0
