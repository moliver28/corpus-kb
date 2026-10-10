"""Release lifecycle service (v6 §6 operations) — the logic the P5 command
surfaces (CLI/MCP/HTTP) will call. Domain + service only: NO surface
registration here (the orchestrator owns surface_registry/cli wiring).

ENFORCEMENT SEMANTICS on ``release`` (v8 ground rule 6, default ``warn``):
  * off      — gate evidence never blocks; the approver is still required;
  * warn     — unmet required gates release WITH explicit warnings returned;
  * enforce  — unmet required gates block (no event emitted).
``assess`` composes gate results into an evidence bundle and writes NOTHING.

The aggregate is the source of truth; ``sink.save(aggregate)`` persists the
event chain (eventsourcing Application in production, an in-memory fake in
tests). The projector lands the read rows asynchronously.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from corpus_kb.domain.codebook import CodebookVersion, ReleaseStateError
from corpus_kb.research.release.events import GATE_STATUSES
from corpus_kb.research.release.gates import GateInputs, GateResult
from corpus_kb.research.release.gates import assess as compose_assess
from corpus_kb.research.release.manifest import ReleaseManifest
from corpus_kb.research.release.profiles import resolve_profile

_ENFORCEMENT_OFF = "off"
_ENFORCEMENT_ENFORCE = "enforce"


class EventSink(Protocol):
    """Persistence seam (eventsourcing Application satisfies it)."""

    def save(self, aggregate: CodebookVersion) -> None: ...


def _release_or_raise(aggregate: CodebookVersion, release_id: UUID) -> dict[str, object]:
    record = aggregate.releases.get(str(release_id))
    if record is None:
        raise ReleaseStateError(f"release {release_id} not found on version {aggregate.label}")
    return record


def _unmet_required(record: dict[str, object]) -> list[str]:
    """Required gates that are neither pass nor waived."""
    profile = resolve_profile(str(record.get("profile")))
    gates_block = record.get("gates")
    waivers_block = record.get("waivers")
    gates: dict[str, object] = gates_block if isinstance(gates_block, dict) else {}
    waivers: dict[str, object] = waivers_block if isinstance(waivers_block, dict) else {}
    unmet: list[str] = []
    for gate_id in profile.required_gates:
        if gate_id in waivers:
            continue
        result = gates.get(gate_id)
        status = result.get("status") if isinstance(result, dict) else None
        if status != "pass":
            unmet.append(gate_id)
    return unmet


class ReleaseService:
    """Lifecycle operations over one CodebookVersion aggregate."""

    def __init__(self, sink: EventSink) -> None:
        self._sink = sink

    def request_release(
        self,
        aggregate: CodebookVersion,
        release_id: UUID,
        codebook_id: UUID,
        profile_name: str | None,
        creator: str,
        manifest: ReleaseManifest,
        requested_at: str,
        parent_release_id: UUID | None = None,
        project_id: UUID | None = None,
    ) -> dict[str, object]:
        """Open a draft_candidate release (idempotent by release_id)."""
        aggregate.request_release(
            tenant_id=aggregate.tenant_id,
            release_id=release_id,
            codebook_id=codebook_id,
            profile=resolve_profile(profile_name).name,
            creator=creator,
            manifest_json=manifest.to_payload(),
            manifest_sha256=manifest.sha256(),
            requested_at=requested_at,
            parent_release_id=parent_release_id,
            project_id=project_id,
            codebook_version_sha256=manifest.codebook_sha256,
        )
        self._sink.save(aggregate)
        return {
            "status": "requested",
            "release_id": str(release_id),
            "manifest_sha256": manifest.sha256(),
            "profile": resolve_profile(profile_name).name,
        }

    def record_gate_result(
        self,
        aggregate: CodebookVersion,
        release_id: UUID,
        result: GateResult,
        evaluated_at: str,
        evaluator: dict[str, object] | None = None,
    ) -> dict[str, object]:
        if result.status not in GATE_STATUSES:
            raise ReleaseStateError(f"invalid gate status: {result.status!r}")
        aggregate.record_gate_result(
            tenant_id=aggregate.tenant_id,
            release_id=release_id,
            gate_id=result.gate_id,
            status=result.status,
            value=_json_safe(result.value),
            threshold=_json_safe(result.threshold),
            reason=result.reason,
            evidence_refs=list(result.evidence_refs),
            evaluator=dict(evaluator or {}),
            evaluated_at=evaluated_at,
        )
        self._sink.save(aggregate)
        return {"status": "recorded", "gate_id": result.gate_id, "gate_status": result.status}

    def record_waiver(
        self,
        aggregate: CodebookVersion,
        release_id: UUID,
        gate_id: str,
        justification: str,
        approver: str,
        recorded_at: str,
    ) -> dict[str, object]:
        aggregate.record_waiver(
            tenant_id=aggregate.tenant_id,
            release_id=release_id,
            gate_id=gate_id,
            justification=justification,
            approver=approver,
            recorded_at=recorded_at,
        )
        self._sink.save(aggregate)
        return {"status": "waived", "gate_id": gate_id}

    @staticmethod
    def assess(inputs: GateInputs, profile_name: str | None) -> dict[str, object]:
        """Evidence bundle over every registered gate; WRITES NO RELEASE."""
        return compose_assess(inputs, resolve_profile(profile_name))

    def release(
        self, aggregate: CodebookVersion, release_id: UUID, approver: str, released_at: str
    ) -> dict[str, object]:
        record = _release_or_raise(aggregate, release_id)
        profile = resolve_profile(str(record.get("profile")))
        unmet = _unmet_required(record)
        if profile.enforcement == _ENFORCEMENT_ENFORCE and unmet:
            return {
                "status": "blocked",
                "release_id": str(release_id),
                "enforcement": profile.enforcement,
                "unmet_required_gates": unmet,
                "reason": "required gates must be pass or waived before release",
            }
        aggregate.release(
            tenant_id=aggregate.tenant_id,
            release_id=release_id,
            approver=approver,
            released_at=released_at,
        )
        self._sink.save(aggregate)
        return {
            "status": "released",
            "release_id": str(release_id),
            "approver": approver,
            "enforcement": profile.enforcement,
            "unmet_required_gates": unmet,
            "warnings": (
                [f"gate {gate_id} unmet at release (enforcement=warn)" for gate_id in unmet]
                if unmet and profile.enforcement != _ENFORCEMENT_OFF
                else []
            ),
        }

    def supersede(
        self,
        aggregate: CodebookVersion,
        release_id: UUID,
        superseded_by_release_id: UUID,
        actor: str,
        superseded_at: str,
    ) -> dict[str, object]:
        aggregate.supersede(
            tenant_id=aggregate.tenant_id,
            release_id=release_id,
            superseded_by_release_id=superseded_by_release_id,
            actor=actor,
            superseded_at=superseded_at,
        )
        self._sink.save(aggregate)
        return {
            "status": "superseded",
            "release_id": str(release_id),
            "superseded_by_release_id": str(superseded_by_release_id),
        }

    def retire(
        self, aggregate: CodebookVersion, release_id: UUID, actor: str, retired_at: str
    ) -> dict[str, object]:
        aggregate.retire(
            tenant_id=aggregate.tenant_id,
            release_id=release_id,
            actor=actor,
            retired_at=retired_at,
        )
        self._sink.save(aggregate)
        return {"status": "retired", "release_id": str(release_id)}

    def propose_change(
        self,
        aggregate: CodebookVersion,
        release_id: UUID,
        successor_release_id: UUID,
        summary: str,
        proposed_by: str,
        proposed_at: str,
        successor_manifest: ReleaseManifest,
        project_id: UUID | None = None,
    ) -> dict[str, object]:
        """U25 data: successor DRAFT linked to the released parent."""
        aggregate.propose_change(
            tenant_id=aggregate.tenant_id,
            release_id=release_id,
            successor_release_id=successor_release_id,
            summary=summary,
            proposed_by=proposed_by,
            proposed_at=proposed_at,
            successor_manifest_json=successor_manifest.to_payload(),
            successor_manifest_sha256=successor_manifest.sha256(),
            project_id=project_id,
            codebook_version_sha256=successor_manifest.codebook_sha256,
        )
        self._sink.save(aggregate)
        return {
            "status": "change_proposed",
            "parent_release_id": str(release_id),
            "successor_release_id": str(successor_release_id),
        }


def _json_safe(value: object) -> dict[str, object] | None:
    """GateResult value/threshold into JSON-safe dicts (None stays None)."""
    if value is None:
        return None
    if isinstance(value, dict):
        return dict(value)
    return {"value": value}
