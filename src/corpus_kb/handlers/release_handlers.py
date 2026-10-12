"""Release-lifecycle surface handlers (U5, P7, v6 §6) — the ONE shared layer.

Every transport calls THESE functions: the CLI group (``corpus-kb release
...`` in ``cli_release.py``), the MCP tool functions
(``tools/release_tools.py``), and the HTTP routes
(``api/research_http.py``). No transport duplicates lifecycle logic: a
handler composes typed payloads once and each transport renders/serializes
them (CLI text or --json, HTTP JSON) — stable schemas pinned by
``tests/test_surface_parity.py``.

Honesty contracts carried here (v6 §6):
  * ``assess_release`` composes EVERY registered gate over the evidence the
    read models can supply and WRITES NOTHING (no draft, no gate result,
    no event); insufficient evidence is reported (``not_evaluable`` +
    ``data_sufficiency`` flags), never upgraded into a pass;
  * ``execute_release`` is the ONLY writing lifecycle step a surface needs:
    it records the freshly evaluated gate results on the DRAFT (deterministic
    pure gate functions), applies any explicit waivers, then executes the
    transition under the profile's enforcement (off|warn|enforce) — a
    ``blocked`` outcome comes back as data, never as a crash;
  * ``status_report`` never invents state: no release row means
    ``pre_draft``, not "released"; ``recommended_next_action`` is always a
    LITERAL runnable ``corpus-kb release ...`` command (guide_copy).

The aggregate is the source of truth: handlers load the CodebookVersion
aggregate through the eventsourcing application (``_load_aggregate``) and
persist via ``ReleaseService`` (EventSink = the application). Read-table
projections may lag; nothing here reads them for lifecycle truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast
from uuid import UUID, uuid4

import asyncpg

from corpus_kb.domain.application import CorpusApplication
from corpus_kb.domain.codebook import CodebookVersion
from corpus_kb.research.release.compare import ReleaseDiff, compare_releases
from corpus_kb.research.release.events import GATE_STATUSES
from corpus_kb.research.release.gate_reads import (
    audit_sample_evidence,
    codebook_categories,
    gate_inputs_from_blocks,
    partitions_evidence,
    run_metric_block,
)
from corpus_kb.research.release.gate_types import GateInputs, GateResult, GateStatus
from corpus_kb.research.release.gates import ALL_RELEASE_GATES
from corpus_kb.research.release.manifest import ReleaseManifest
from corpus_kb.research.release.profiles import resolve_profile
from corpus_kb.research.release.service import ReleaseService

# Lifecycle stages (derived, honest; labels rendered via guide_copy).
STAGE_PRE_DRAFT = "pre_draft"
STAGE_GATE_EVIDENCE = "gate_evidence"
STAGE_READY_FOR_RELEASE = "ready_for_release"
STAGE_RELEASED = "released"
STAGE_CLOSED = "closed"

_STATE_DRAFT = "draft_candidate"
_STATE_RELEASED = "released"


# ---------------------------------------------------------------------------
# Typed payloads (stable schemas — D4; pinned by tests/test_surface_parity.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssessResult:
    """The assess evidence bundle (writes nothing)."""

    codebook_version_id: str
    profile: str
    enforcement: str
    gate_results: list[dict[str, object]]
    required_gate_ids: list[str]
    required_failing: list[str]
    required_not_evaluable: list[str]
    all_required_pass_without_waivers: bool
    data_sufficiency: dict[str, bool] = field(default_factory=dict)

    def to_payload(self) -> dict[str, object]:
        return {
            "status": "assessed",
            "codebook_version_id": self.codebook_version_id,
            "profile": self.profile,
            "enforcement": self.enforcement,
            "gate_results": list(self.gate_results),
            "required_gate_ids": list(self.required_gate_ids),
            "required_failing": list(self.required_failing),
            "required_not_evaluable": list(self.required_not_evaluable),
            "all_required_pass_without_waivers": self.all_required_pass_without_waivers,
            "data_sufficiency": dict(self.data_sufficiency),
        }


@dataclass(frozen=True)
class RequestResult:
    """One draft_candidate opened (idempotent by release id)."""

    status: str
    release_id: str
    manifest_sha256: str
    profile: str

    def to_payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "release_id": self.release_id,
            "manifest_sha256": self.manifest_sha256,
            "profile": self.profile,
        }


@dataclass(frozen=True)
class ReviewPacket:
    """The review-release packet: evidence, failures, waivers, next actions."""

    release_id: str
    release_state: str
    profile: str
    enforcement: str
    evidence: list[dict[str, object]]
    failing_gates: list[str]
    not_evaluable_gates: list[str]
    waived_gates: list[str]
    unmet_required_gates: list[str]
    next_actions: list[str]
    recommended_next_action: str

    def to_payload(self) -> dict[str, object]:
        return {
            "status": "review_packet",
            "release_id": self.release_id,
            "release_state": self.release_state,
            "profile": self.profile,
            "enforcement": self.enforcement,
            "evidence": list(self.evidence),
            "failing_gates": list(self.failing_gates),
            "not_evaluable_gates": list(self.not_evaluable_gates),
            "waived_gates": list(self.waived_gates),
            "unmet_required_gates": list(self.unmet_required_gates),
            "next_actions": list(self.next_actions),
            "recommended_next_action": self.recommended_next_action,
        }


@dataclass(frozen=True)
class ReleaseOutcome:
    """The release transition receipt (``released`` or ``blocked``)."""

    status: str
    release_id: str
    approver: str
    enforcement: str
    unmet_required_gates: list[str]
    warnings: list[str]
    reason: str = ""

    def to_payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "release_id": self.release_id,
            "approver": self.approver,
            "enforcement": self.enforcement,
            "unmet_required_gates": list(self.unmet_required_gates),
            "warnings": list(self.warnings),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class StatusReport:
    """The release status view (D1 fields + sufficiency flags)."""

    codebook_version_id: str
    current_stage: str
    release_state: str
    blocking_criteria: list[str]
    recommended_next_action: str
    active_release: dict[str, object] | None
    draft: dict[str, object] | None
    pending_change_proposals: list[dict[str, object]]
    data_sufficiency: dict[str, bool] = field(default_factory=dict)

    def to_payload(self) -> dict[str, object]:
        return {
            "status": "ok",
            "codebook_version_id": self.codebook_version_id,
            "current_stage": self.current_stage,
            "release_state": self.release_state,
            "blocking_criteria": list(self.blocking_criteria),
            "recommended_next_action": self.recommended_next_action,
            "active_release": self.active_release,
            "draft": self.draft,
            "pending_change_proposals": list(self.pending_change_proposals),
            "data_sufficiency": dict(self.data_sufficiency),
        }


@dataclass(frozen=True)
class CompareResult:
    """The manifest diff between two releases of one version."""

    old_release_id: str
    new_release_id: str
    diff: dict[str, object]

    @property
    def identical(self) -> bool:
        return bool(self.diff.get("identical"))

    def to_payload(self) -> dict[str, object]:
        return {
            "status": "compared",
            "old_release_id": self.old_release_id,
            "new_release_id": self.new_release_id,
            **self.diff,
        }


# ---------------------------------------------------------------------------
# Pure stage derivation + next-action selection (guide_copy literals)
# ---------------------------------------------------------------------------


def unmet_required_gates(record: dict[str, object]) -> list[str]:
    """Required gates that are neither pass nor waived on one release record."""
    profile = resolve_profile(str(record.get("profile")))
    gates = record.get("gates")
    waivers = record.get("waivers")
    gate_map = gates if isinstance(gates, dict) else {}
    waiver_map = waivers if isinstance(waivers, dict) else {}
    unmet: list[str] = []
    for gate_id in profile.required_gates:
        if gate_id in waiver_map:
            continue
        result = gate_map.get(gate_id)
        status = result.get("status") if isinstance(result, dict) else None
        if status != "pass":
            unmet.append(gate_id)
    return unmet


def release_stage(record: dict[str, object] | None) -> str:
    """Map one release record (or None) to its honest lifecycle stage."""
    if record is None:
        return STAGE_PRE_DRAFT
    state = str(record.get("state"))
    if state == _STATE_DRAFT:
        return STAGE_READY_FOR_RELEASE if not unmet_required_gates(record) else STAGE_GATE_EVIDENCE
    if state == _STATE_RELEASED:
        return STAGE_RELEASED
    return STAGE_CLOSED


def recommended_next_action(stage: str) -> str:
    """The LITERAL runnable command for the stage (guide_copy is the source)."""
    from corpus_kb.research import guide_copy

    return {
        STAGE_PRE_DRAFT: guide_copy.RELEASE_NEXT_REQUEST,
        STAGE_GATE_EVIDENCE: guide_copy.RELEASE_NEXT_ASSESS,
        STAGE_READY_FOR_RELEASE: guide_copy.RELEASE_NEXT_RELEASE,
        STAGE_RELEASED: guide_copy.RELEASE_NEXT_COMPARE,
        STAGE_CLOSED: guide_copy.RELEASE_NEXT_STATUS,
    }.get(stage, guide_copy.RELEASE_NEXT_STATUS)


# ---------------------------------------------------------------------------
# Evidence composition (read-only; the seam tests fake out)
# ---------------------------------------------------------------------------

_AUDIT_ALL_SQL = (
    "SELECT scope, unit_id, partition, stratum, inclusion_probability, seed, "
    "sampled_at FROM audit_partitions WHERE tenant_id = $1 ORDER BY audit_id"
)


async def _audit_rows_all(pool: asyncpg.Pool, tenant_id: UUID) -> list[dict[str, object]]:
    """Every audit_partitions row for the tenant (no scope filter: the U9
    partition evidence and the U17 audit sample split off the same rows)."""
    from corpus_kb.storage.tenant_conn import tenant_connection

    async with tenant_connection(pool, tenant_id) as conn:
        rows = await conn.fetch(_AUDIT_ALL_SQL, tenant_id)
    mapped: list[dict[str, object]] = []
    for row in rows:
        inclusion = row["inclusion_probability"]
        seed = row["seed"]
        sampled_at = row["sampled_at"]
        mapped.append(
            {
                "scope": str(row["scope"]),
                "unit_id": str(row["unit_id"]),
                "partition": str(row["partition"]),
                "stratum": str(row["stratum"]) if row["stratum"] is not None else None,
                "inclusion_probability": float(inclusion) if inclusion is not None else None,
                "seed": int(seed) if seed is not None else None,
                "sampled_at": str(sampled_at) if sampled_at is not None else None,
            }
        )
    return mapped


async def gate_inputs_for_version(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    codebook_version_id: UUID,
    run_id: UUID | None = None,
) -> tuple[GateInputs, dict[str, bool]]:
    """Assemble the mechanically readable evidence blocks (read-only).

    Blocks the read models cannot supply without more caller context
    (prompt variants, PPI, held-out, determinism proof, canary, consensus)
    stay ABSENT: their gates answer ``not_evaluable`` — the honesty
    contract. ``run_id`` opts in the run-checkpoint blocks (reliability).
    """
    blocks: dict[str, object] = {}
    sufficiency: dict[str, bool] = {}
    codebook = await codebook_categories(pool, tenant_id, codebook_version_id)
    blocks["codebook"] = codebook
    sufficiency["codebook"] = bool(codebook.get("categories"))
    rows = await _audit_rows_all(pool, tenant_id)
    blocks["partitions"] = partitions_evidence(rows)
    sufficiency["partitions"] = bool(rows)
    blocks["audit_sample"] = audit_sample_evidence(rows)
    sufficiency["audit_sample"] = bool(
        isinstance(blocks["audit_sample"], dict) and (blocks["audit_sample"].get("rows"))
    )
    if run_id is not None:
        reliability = await run_metric_block(pool, tenant_id, run_id, "reliability")
        blocks["reliability"] = reliability
        sufficiency["reliability"] = bool(reliability)
    return gate_inputs_from_blocks(blocks), sufficiency


# ---------------------------------------------------------------------------
# Aggregate access (the seams tests monkeypatch)
# ---------------------------------------------------------------------------


def _conn_str() -> str:
    from corpus_kb.config import load_config

    db = load_config().get("database") or {}
    return str(db.get("connection_string", ""))


def _load_aggregate(conn_str: str, codebook_version_id: UUID) -> CodebookVersion:
    from corpus_kb.domain.application import get_app

    return get_app(conn_str).repository.get(codebook_version_id)


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _record_or_raise(aggregate: CodebookVersion, release_id: UUID) -> dict[str, object]:
    record = aggregate.releases.get(str(release_id))
    if record is None:
        raise ValueError(f"release {release_id} not found on codebook version {aggregate.id}")
    return record


def _dict_or_empty(value: object) -> dict[str, object]:
    """Event-store JSON blocks arrive as `object`; coerce honestly (None/other -> {})."""
    return dict(value) if isinstance(value, dict) else {}


def _manifest_from_payload(payload: dict[str, object], release_id: str) -> ReleaseManifest:
    """Rebuild the typed manifest from a stored manifest_json payload."""
    if not payload:
        raise ValueError(f"release {release_id} carries no manifest payload")
    timestamps = _dict_or_empty(payload.get("timestamps"))
    codes_raw = payload.get("codes")
    return ReleaseManifest(
        release_id=str(payload.get("release_id", release_id)),
        codebook_id=str(payload.get("codebook_id", "")),
        codebook_version=str(payload.get("codebook_version", "")),
        codebook_sha256=str(payload.get("codebook_sha256", "")),
        profile=_dict_or_empty(payload.get("profile")),
        method_declaration=_dict_or_empty(payload.get("method_declaration")),
        machine_profile=_dict_or_empty(payload.get("machine_profile")),
        created_at=str(timestamps.get("created_at", "")),
        codes=[dict(code) for code in codes_raw] if isinstance(codes_raw, list) else [],
        parent_release_id=(
            str(payload["parent_release_id"]) if payload.get("parent_release_id") else None
        ),
        metric_snapshots=_dict_or_empty(payload.get("metric_snapshots")),
    )


def _manifest_for_version(
    aggregate: CodebookVersion, release_id: UUID, profile_name: str | None
) -> ReleaseManifest:
    """The draft manifest over the aggregate's CURRENT codebook content.

    ``machine_profile`` stays empty until a run records one — an empty block
    keeps the machine-profile gate honestly ``not_evaluable`` instead of
    fabricating a profile.
    """
    profile = resolve_profile(profile_name)
    codes = [
        {
            "name": str(code.get("name", code_id)),
            "definition": str(code.get("definition", "")),
        }
        for code_id, code in sorted(aggregate.codes.items())
    ]
    return ReleaseManifest(
        release_id=str(release_id),
        codebook_id=str(aggregate.id),
        codebook_version=str(aggregate.id),
        codebook_sha256=str(aggregate.sha256),
        profile={"name": profile.name},
        method_declaration={"paradigm": str(aggregate.paradigm)},
        machine_profile={},
        created_at=_now_iso(),
        codes=codes,
    )


# ---------------------------------------------------------------------------
# The six handlers (one per surface operation)
# ---------------------------------------------------------------------------


async def assess_release(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    codebook_version_id: UUID,
    profile_name: str | None = None,
    run_id: UUID | None = None,
) -> AssessResult:
    """Compose every gate over current evidence; WRITES NO RELEASE (v6 §6)."""
    inputs, sufficiency = await gate_inputs_for_version(
        pool, tenant_id, codebook_version_id, run_id
    )
    bundle = ReleaseService.assess(inputs, profile_name)
    return AssessResult(
        codebook_version_id=str(codebook_version_id),
        profile=str(bundle.get("profile", "")),
        enforcement=str(bundle.get("enforcement", "")),
        gate_results=list(bundle.get("gate_results") or []),
        required_gate_ids=list(bundle.get("required_gate_ids") or []),
        required_failing=list(bundle.get("required_failing") or []),
        required_not_evaluable=list(bundle.get("required_not_evaluable") or []),
        all_required_pass_without_waivers=bool(bundle.get("all_required_pass_without_waivers")),
        data_sufficiency=sufficiency,
    )


async def request_release(
    pool: asyncpg.Pool,  # transport-signature symmetry; requests do no pool reads
    tenant_id: UUID,
    codebook_version_id: UUID,
    creator: str,
    codebook_id: UUID | None = None,
    profile_name: str | None = None,
    release_id: UUID | None = None,
) -> RequestResult:
    """Open a draft_candidate release pinned to the current content hash."""
    aggregate = _load_aggregate(_conn_str(), codebook_version_id)
    if str(aggregate.tenant_id) != str(tenant_id):
        raise ValueError(f"codebook version {codebook_version_id} is not in tenant {tenant_id}")
    app = _app_sink()
    rid = release_id or uuid4()
    receipt = ReleaseService(app).request_release(
        aggregate,
        release_id=rid,
        codebook_id=codebook_id or codebook_version_id,
        profile_name=profile_name,
        creator=creator,
        manifest=_manifest_for_version(aggregate, rid, profile_name),
        requested_at=_now_iso(),
        project_id=None,
    )
    return RequestResult(
        status=str(receipt.get("status", "requested")),
        release_id=str(receipt.get("release_id", "")),
        manifest_sha256=str(receipt.get("manifest_sha256", "")),
        profile=str(receipt.get("profile", "")),
    )


def _app_sink() -> CorpusApplication:
    """The eventsourcing Application as the ReleaseService EventSink."""
    from corpus_kb.domain.application import get_app

    return get_app(_conn_str())


async def review_release(
    pool: asyncpg.Pool,  # transport-signature symmetry; packets read aggregates
    tenant_id: UUID,
    codebook_version_id: UUID,
    release_id: UUID,
) -> ReviewPacket:
    """The review packet (v6 §6): evidence, failing + not_evaluable gates,
    waivers, and the SPECIFIC next actions (guide_copy literal commands)."""
    from corpus_kb.research import guide_copy

    aggregate = _load_aggregate(_conn_str(), codebook_version_id)
    if str(aggregate.tenant_id) != str(tenant_id):
        raise ValueError(f"codebook version {codebook_version_id} is not in tenant {tenant_id}")
    record = _record_or_raise(aggregate, release_id)
    profile = resolve_profile(str(record.get("profile")))
    gates = record.get("gates")
    gate_map = gates if isinstance(gates, dict) else {}
    waivers = record.get("waivers")
    waiver_map = waivers if isinstance(waivers, dict) else {}
    evidence = [
        {
            "gate_id": gate_id,
            "status": str(result.get("status", "")),
            "reason": str(result.get("reason", "")),
            "value": result.get("value"),
            "threshold": result.get("threshold"),
        }
        for gate_id, result in sorted(gate_map.items())
        if isinstance(result, dict)
    ]
    failing = sorted(
        gid for gid, r in gate_map.items() if isinstance(r, dict) and r.get("status") == "fail"
    )
    not_evaluable = sorted(
        gid
        for gid, r in gate_map.items()
        if isinstance(r, dict) and r.get("status") == "not_evaluable"
    )
    waived = sorted(waiver_map)
    unmet = unmet_required_gates(record)
    state = str(record.get("state"))
    next_actions: list[str] = []
    if state == _STATE_DRAFT and (failing or not_evaluable):
        next_actions.append(guide_copy.RELEASE_NEXT_ASSESS)
    if state == _STATE_DRAFT and unmet:
        next_actions.append(guide_copy.RELEASE_NEXT_RELEASE)
    if state == _STATE_RELEASED:
        next_actions.append(guide_copy.RELEASE_NEXT_COMPARE)
    if not next_actions:
        next_actions.append(guide_copy.RELEASE_NEXT_STATUS)
    return ReviewPacket(
        release_id=str(release_id),
        release_state=state,
        profile=profile.name,
        enforcement=profile.enforcement,
        evidence=evidence,
        failing_gates=failing,
        not_evaluable_gates=not_evaluable,
        waived_gates=waived,
        unmet_required_gates=unmet,
        next_actions=next_actions,
        recommended_next_action=next_actions[0],
    )


async def execute_release(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    codebook_version_id: UUID,
    release_id: UUID,
    approver: str,
    waivers: tuple[tuple[str, str], ...] = (),
    run_id: UUID | None = None,
    config: dict[str, object] | None = None,
) -> ReleaseOutcome:
    """Evaluate, record, waive (explicitly), then execute the transition.

    The gate results recorded here come from the SAME pure gate functions
    ``assess`` reports — recording is bookkeeping, never new evidence. A
    ``blocked`` outcome is DATA (enforcement=enforce refused); the caller
    decides the transport presentation (CLI exit 1, HTTP 409). Enforcement
    comes from ``research.release.enforcement`` in ``config`` (falls back
    to the loaded config when None).
    """
    app = _app_sink()
    aggregate = _load_aggregate(_conn_str(), codebook_version_id)
    if str(aggregate.tenant_id) != str(tenant_id):
        raise ValueError(f"codebook version {codebook_version_id} is not in tenant {tenant_id}")
    record = _record_or_raise(aggregate, release_id)
    service = ReleaseService(app, config=config)
    inputs, _sufficiency = await gate_inputs_for_version(
        pool, tenant_id, codebook_version_id, run_id
    )
    bundle = ReleaseService.assess(inputs, str(record.get("profile")))
    raw_results = bundle.get("gate_results")
    evaluated = (
        [item for item in raw_results if isinstance(item, dict)]
        if isinstance(raw_results, list)
        else []
    )
    for result in evaluated:
        status = str(result.get("status", ""))
        if status not in GATE_STATUSES:
            raise ValueError(f"gate evaluation returned invalid status {status!r}")
        service.record_gate_result(
            aggregate,
            release_id,
            GateResult(
                gate_id=str(result.get("gate_id", "")),
                status=cast("GateStatus", status),
                reason=str(result.get("reason", "")),
                value=result.get("value"),
                threshold=result.get("threshold"),
            ),
            evaluated_at=_now_iso(),
            evaluator={"surface": "release_execute"},
        )
    for gate_id, justification in waivers:
        if gate_id not in ALL_RELEASE_GATES:
            raise ValueError(
                f"unknown release gate {gate_id!r} (known: {sorted(ALL_RELEASE_GATES)})"
            )
        service.record_waiver(aggregate, release_id, gate_id, justification, approver, _now_iso())
    outcome = service.release(aggregate, release_id, approver=approver, released_at=_now_iso())
    return ReleaseOutcome(
        status=str(outcome.get("status", "")),
        release_id=str(outcome.get("release_id", "")),
        approver=str(outcome.get("approver", approver)),
        enforcement=str(outcome.get("enforcement", "")),
        unmet_required_gates=list(outcome.get("unmet_required_gates") or []),
        warnings=list(outcome.get("warnings") or []),
        reason=str(outcome.get("reason", "")),
    )


async def status_report(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    codebook_version_id: UUID,
) -> StatusReport:
    """Active release, draft state, pending change proposals, next action."""
    aggregate = _load_aggregate(_conn_str(), codebook_version_id)
    records = [
        record
        for record in aggregate.releases.values()
        if record.get("tenant_id") == str(tenant_id)
    ]
    drafts = sorted(
        (r for r in records if r.get("state") == _STATE_DRAFT),
        key=lambda r: str(r.get("created_at", "")),
        reverse=True,
    )
    actives = sorted(
        (r for r in records if r.get("state") == _STATE_RELEASED),
        key=lambda r: str(r.get("released_at", "")),
        reverse=True,
    )
    primary = drafts[0] if drafts else (actives[0] if actives else None)
    stage = release_stage(primary)
    blocking: list[str] = []
    if primary is not None and stage == STAGE_GATE_EVIDENCE:
        gates = primary.get("gates")
        gate_map = gates if isinstance(gates, dict) else {}
        for gate_id in unmet_required_gates(primary):
            result = gate_map.get(gate_id)
            status = result.get("status") if isinstance(result, dict) else "not_evaluated"
            blocking.append(f"{gate_id}: {status}")
    _inputs, sufficiency = await gate_inputs_for_version(pool, tenant_id, codebook_version_id)

    def _brief(record: dict[str, object]) -> dict[str, object]:
        return {
            "release_id": str(record.get("release_id", "")),
            "profile": str(record.get("profile", "")),
            "manifest_sha256": str(record.get("manifest_sha256", "")),
            "approver": record.get("approver"),
            "released_at": record.get("released_at"),
        }

    proposals = [
        {
            "release_id": str(record.get("release_id", "")),
            "parent_release_id": str(record.get("parent_release_id", "")),
            "creator": str(record.get("creator", "")),
            "created_at": str(record.get("created_at", "")),
        }
        for record in drafts
        if record.get("parent_release_id")
    ]
    return StatusReport(
        codebook_version_id=str(codebook_version_id),
        current_stage=stage,
        release_state=str(primary.get("state")) if primary else "none",
        blocking_criteria=blocking,
        recommended_next_action=recommended_next_action(stage),
        active_release=_brief(actives[0]) if actives else None,
        draft=_brief(drafts[0]) if drafts else None,
        pending_change_proposals=proposals,
        data_sufficiency=sufficiency,
    )


async def compare_version_releases(
    tenant_id: UUID,
    codebook_version_id: UUID,
    old_release_id: UUID,
    new_release_id: UUID,
) -> CompareResult:
    """Diff two releases' manifests (pure; no pool, no writes)."""
    aggregate = _load_aggregate(_conn_str(), codebook_version_id)
    if str(aggregate.tenant_id) != str(tenant_id):
        raise ValueError(f"codebook version {codebook_version_id} is not in tenant {tenant_id}")
    old_record = _record_or_raise(aggregate, old_release_id)
    new_record = _record_or_raise(aggregate, new_release_id)
    old_manifest = _manifest_from_payload(
        dict(old_record.get("manifest_json") or {}), str(old_release_id)
    )
    new_manifest = _manifest_from_payload(
        dict(new_record.get("manifest_json") or {}), str(new_release_id)
    )
    diff: ReleaseDiff = compare_releases(old_manifest, new_manifest)
    return CompareResult(
        old_release_id=str(old_release_id),
        new_release_id=str(new_release_id),
        diff=diff.to_dict(),
    )
