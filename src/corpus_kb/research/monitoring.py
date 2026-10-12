"""U25 monitoring → change proposals (v6 §9): alarms NEVER mutate releases.

The cycle's existing alarms (drift, residual/overlap structure, threshold
reliability, human-parity calibration, gray-zone routing) become
``CodebookChangeProposed`` events via ``ReleaseService.propose_change``: a
successor DRAFT release linked to the released parent. The parent's state,
codebook hash, and manifest are untouched — the aggregate's own contract
guarantees it, and the tests pin it.

Honesty rules:
  * no released release for the version → ``no_active_release`` (there is
    nothing to propose against; monitoring never fabricates a parent);
  * no monitored alarm fired → ``no_alarms`` (no proposal is invented);
  * the successor manifest carries the SAME codebook identity (codes and
    codebook_sha256 unchanged) — monitoring proposes REVIEW of the codebook,
    never silent content changes; the alarm block rides in
    ``metric_snapshots.monitoring_alarms``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from corpus_kb.research.cycle_gates import GateFinding
from corpus_kb.research.deduction_guard import ActiveRelease
from corpus_kb.research.release.manifest import ReleaseManifest
from corpus_kb.research.release.service import ReleaseService

# Monitored alarm gate -> alarm kind (U25: drift / residual / reliability /
# calibration / routing). codebook_promotion and interpretive_code_review
# are deliberately absent: the first is the human gate itself, the second a
# design posture (interpretive codes ALWAYS route to review), not a defect
# signal.
MONITORED_ALARMS: dict[str, str] = {
    "drift_alarm": "drift",
    "overlap_conflict": "residual",
    "threshold_unreliable": "reliability",
    "human_parity_breach": "calibration",
    "gray_zone_escalation": "routing",
}

MONITOR_ACTOR = "monitoring:cycle"


@dataclass(frozen=True)
class Alarm:
    """One monitored alarm raised by a cycle gate finding."""

    gate: str
    kind: str
    reason: str
    detail: dict[str, object]

    def block(self) -> dict[str, object]:
        return {"gate": self.gate, "kind": self.kind, "reason": self.reason, **_flat(self.detail)}


def _flat(detail: dict[str, object]) -> dict[str, object]:
    """Keep the alarm block JSON-plain (scalars and short scalar lists)."""
    flat: dict[str, object] = {}
    for key, value in detail.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            flat[key] = value
        elif isinstance(value, list):
            flat[key] = [v for v in value if isinstance(v, (str, int, float, bool))][:12]
    return flat


def alarms_from_findings(findings: list[GateFinding]) -> list[Alarm]:
    """Project cycle gate findings onto the monitored alarm kinds."""
    alarms: list[Alarm] = []
    for finding in findings:
        kind = MONITORED_ALARMS.get(finding.gate)
        if kind is None:
            continue
        alarms.append(
            Alarm(gate=finding.gate, kind=kind, reason=finding.reason, detail=finding.detail)
        )
    return alarms


def alarms_block(alarms: list[Alarm]) -> dict[str, object]:
    """The ``metric_snapshots.monitoring_alarms`` block for the successor."""
    return {
        "source": MONITOR_ACTOR,
        "n_alarms": len(alarms),
        "alarms": [alarm.block() for alarm in alarms],
    }


def _string_keyed(payload: dict[str, Any], key: str) -> dict[str, object]:
    """A nested manifest block as a plain str-keyed dict (missing -> {})."""
    raw = payload.get(key)
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items()}


def successor_manifest(
    parent: ReleaseManifest | dict[str, Any],
    successor_release_id: UUID,
    alarms: list[Alarm],
    created_at: str,
) -> ReleaseManifest:
    """The successor DRAFT manifest: identical codebook, alarms attached.

    Accepts a ``ReleaseManifest`` or its ``to_payload()`` dict (the released
    row's ``manifest_json``). Codes, codebook ids, and the codebook content
    hash are copied VERBATIM — monitoring never edits codebook content.
    """
    payload = parent.to_payload() if isinstance(parent, ReleaseManifest) else dict(parent)
    base_snapshots = _string_keyed(payload, "metric_snapshots")
    base_snapshots["monitoring_alarms"] = alarms_block(alarms)
    codes_raw = payload.get("codes")
    codes = [dict(c) for c in codes_raw] if isinstance(codes_raw, list) else []
    return ReleaseManifest(
        release_id=str(successor_release_id),
        codebook_id=str(payload["codebook_id"]),
        codebook_version=str(payload["codebook_version"]),
        codebook_sha256=str(payload["codebook_sha256"]),
        profile=_string_keyed(payload, "profile"),
        method_declaration=_string_keyed(payload, "method_declaration"),
        machine_profile=_string_keyed(payload, "machine_profile"),
        created_at=created_at,
        codes=codes,
        parent_release_id=str(payload["release_id"]),
        metric_snapshots=base_snapshots,
    )


def summary_for(alarms: list[Alarm], version_id: UUID) -> str:
    """The one-line proposal summary (gate kinds, honest and short)."""
    kinds = ", ".join(sorted({alarm.kind for alarm in alarms}))
    return f"monitoring alarms on version {version_id}: {kinds}"


def propose_from_alarms(
    aggregate: Any,
    parent_release_id: UUID,
    parent_manifest: ReleaseManifest | dict[str, Any],
    alarms: list[Alarm],
    version_id: UUID,
    *,
    sink: Any,
    proposed_by: str = MONITOR_ACTOR,
    proposed_at: str = "",
    project_id: UUID | None = None,
) -> dict[str, object]:
    """Mint ONE successor draft linked to the released parent (U25).

    Calls ``ReleaseService.propose_change`` on the aggregate; the parent's
    released state, manifest hash, and codebook hash are never mutated (the
    aggregate raises on any violating transition). Returns the receipt the
    cycle run-report payload surfaces.
    """
    if not alarms:
        return {"status": "no_alarms", "reason": "no monitored alarm fired this cycle"}
    successor_id = uuid4()
    manifest = successor_manifest(parent_manifest, successor_id, alarms, proposed_at)
    service = ReleaseService(sink)
    outcome = service.propose_change(
        aggregate,
        release_id=parent_release_id,
        successor_release_id=successor_id,
        summary=summary_for(alarms, version_id),
        proposed_by=proposed_by,
        proposed_at=proposed_at,
        successor_manifest=manifest,
        project_id=project_id,
    )
    return {
        **outcome,
        "codebook_version_id": str(version_id),
        "alarms": [alarm.block() for alarm in alarms],
        "successor_manifest_sha256": manifest.sha256(),
    }


async def active_release_or_none(
    pool: Any, tenant_id: UUID, version_id: UUID | None
) -> ActiveRelease | None:
    """Resolve the active release for the cycle's codebook version."""
    from corpus_kb.research.deduction_guard import resolve_active_release

    if version_id is None:
        return None
    return await resolve_active_release(pool, tenant_id, version_id)
