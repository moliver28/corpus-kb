"""CodebookVersion aggregate (todo-11 (a)) — ONE instance PER PROMOTION.

Events: Created, CodeAdded, DefinitionRefined, PrototypeUpdated,
ThresholdRecalibrated, KeywordSetUpdated, plus the release lifecycle
(U1, v6 §6): CodebookReleaseRequested, GateEvaluated, WaiverRecorded,
CodebookReleased, CodebookSuperseded, CodebookChangeProposed, and the
U48 reviewer-rubric event ReviewerRubricRecorded. A version's full state
is the replay of its own event chain; the NEXT promotion mints a NEW
aggregate instance (immutable version history).

PrototypeUpdated carries exemplar text_sha256 REFS, never vectors — vectors
are derived data rebuilt through the embedding cache.

RELEASE CONTRACT: a ``released`` release is immutable. The only permitted
transitions out of it are the guarded ``supersede`` / ``retire`` state
transitions (never an edit) and ``propose_change``, which mints a successor
DRAFT linked to the released parent. Gate results and waivers attach only
while a release is still ``draft_candidate``.

TENANT CONTRACT (r8): every event signature carries tenant_id explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from eventsourcing.domain import Aggregate, event

MAX_CODES_PER_EVENT = 100
MAX_KEYWORDS_PER_EVENT = 200

RELEASE_DRAFT = "draft_candidate"
RELEASE_RELEASED = "released"
RELEASE_SUPERSEDED = "superseded"
RELEASE_RETIRED = "retired"


class ReleaseStateError(ValueError):
    """A release lifecycle transition violated the aggregate's contract."""


@dataclass
class CodebookVersion(Aggregate):
    """One codebook version's authoritative event chain."""

    tenant_id: UUID
    label: str
    sha256: str
    paradigm: str = "deductive"
    notes: str = ""
    codes: dict[str, dict[str, object]] = field(default_factory=dict)
    releases: dict[str, dict[str, object]] = field(default_factory=dict)
    rubric_reviews: dict[str, dict[str, object]] = field(default_factory=dict)

    @event("Created")
    def __init__(
        self,
        tenant_id: UUID,
        label: str,
        sha256: str,
        paradigm: str = "deductive",
        notes: str = "",
    ) -> None:
        self.tenant_id = tenant_id
        self.label = label
        self.sha256 = sha256
        self.paradigm = paradigm
        self.notes = notes
        self.codes = {}
        self.releases = {}
        self.rubric_reviews = {}

    @event("CodeAdded")
    def add_codes(self, tenant_id: UUID, codes: list[dict[str, object]]) -> None:
        """Add one batch of codes; each carries a caller-minted code_id."""
        if not 0 < len(codes) <= MAX_CODES_PER_EVENT:
            raise ValueError(f"code batch must be 1..{MAX_CODES_PER_EVENT}, got {len(codes)}")
        for code in codes:
            code_id = str(code.get("code_id", ""))
            if not code_id or not code.get("name") or not code.get("definition"):
                raise ValueError("code requires code_id, name, definition")
            code["tenant_id"] = str(tenant_id)
            self.codes[code_id] = dict(code)

    @event("DefinitionRefined")
    def refine_definition(
        self, tenant_id: UUID, code_id: UUID, definition: str, inclusion: str, exclusion: str
    ) -> None:
        """Refine one code's definition in THIS version."""
        code = self._code(str(code_id))
        code.update(
            {
                "tenant_id": str(tenant_id),
                "definition": definition,
                "inclusion": inclusion,
                "exclusion": exclusion,
            }
        )

    @event("PrototypeUpdated")
    def update_prototypes(
        self, tenant_id: UUID, code_id: UUID, exemplar_text_sha256: list[str]
    ) -> None:
        """Point one code at its gold exemplar texts (vector rebuild is derived)."""
        code = self._code(str(code_id))
        code["tenant_id"] = str(tenant_id)
        code["exemplar_text_sha256"] = list(exemplar_text_sha256)

    @event("ThresholdRecalibrated")
    def recalibrate_thresholds(
        self,
        tenant_id: UUID,
        code_id: UUID,
        tau_a: float,
        tau_qa: float,
        delta: float,
        n_gold: int,
        cv_range: dict[str, object],
    ) -> None:
        """Store fitted per-code thresholds (G2 output) on THIS version."""
        code = self._code(str(code_id))
        code.update(
            {
                "tenant_id": str(tenant_id),
                "tau_a": tau_a,
                "tau_qa": tau_qa,
                "delta": delta,
                "n_gold": n_gold,
                "cv_range": cv_range,
                "threshold_unreliable": n_gold < 20,
            }
        )

    @event("KeywordSetUpdated")
    def update_keywords(
        self, tenant_id: UUID, code_id: UUID, keywords: list[dict[str, object]]
    ) -> None:
        """Set one code's inclusion/exclusion keyword list (todo-17 synthesizes)."""
        if len(keywords) > MAX_KEYWORDS_PER_EVENT:
            raise ValueError(f"keyword batch > {MAX_KEYWORDS_PER_EVENT}")
        code = self._code(str(code_id))
        code["tenant_id"] = str(tenant_id)
        code["keywords"] = [dict(k) for k in keywords]

    def _code(self, code_id: str) -> dict[str, object]:
        code = self.codes.get(code_id)
        if code is None:
            raise ValueError(f"code {code_id} not in version {self.label}")
        return code

    # ------------------------------------------------------------------
    # Release lifecycle (U1, v6 §6) — event names match research/release/
    # events.py; guards mirror migration 019's immutability trigger.
    # ------------------------------------------------------------------

    def _release(self, tenant_id: UUID, release_id: UUID) -> dict[str, object]:
        release = self.releases.get(str(release_id))
        if release is None or release.get("tenant_id") != str(tenant_id):
            raise ReleaseStateError(f"release {release_id} not found for tenant")
        return release

    @staticmethod
    def _release_map(release: dict[str, object], key: str) -> dict[str, dict[str, object]]:
        """A release's nested gate/waiver map, validated on replay too."""
        nested = release.get(key)
        if not isinstance(nested, dict):
            raise ReleaseStateError(f"release record is missing its {key!r} map")
        return nested

    @event("CodebookReleaseRequested")
    def request_release(
        self,
        tenant_id: UUID,
        release_id: UUID,
        codebook_id: UUID,
        profile: str,
        creator: str,
        manifest_json: dict[str, object],
        manifest_sha256: str,
        requested_at: str,
        parent_release_id: UUID | None = None,
    ) -> None:
        """Open a draft_candidate release for THIS version (idempotent id)."""
        if str(release_id) in self.releases:
            raise ReleaseStateError(f"release {release_id} already exists on this version")
        self.releases[str(release_id)] = {
            "tenant_id": str(tenant_id),
            "release_id": str(release_id),
            "codebook_id": str(codebook_id),
            "state": RELEASE_DRAFT,
            "profile": profile,
            "parent_release_id": (
                str(parent_release_id) if parent_release_id is not None else None
            ),
            "manifest_json": dict(manifest_json),
            "manifest_sha256": manifest_sha256,
            "created_at": requested_at,
            "creator": creator,
            "approver": None,
            "released_at": None,
            "gates": {},
            "waivers": {},
        }

    @event("GateEvaluated")
    def record_gate_result(
        self,
        tenant_id: UUID,
        release_id: UUID,
        gate_id: str,
        status: str,
        value: dict[str, object] | None,
        threshold: dict[str, object] | None,
        reason: str,
        evidence_refs: list[str],
        evaluator: dict[str, object],
        evaluated_at: str,
    ) -> None:
        """Record one gate result; re-evaluations overwrite while still a draft."""
        release = self._release(tenant_id, release_id)
        if release["state"] != RELEASE_DRAFT:
            raise ReleaseStateError(
                f"release {release_id} is {release['state']}; gates attach to drafts only"
            )
        self._release_map(release, "gates")[gate_id] = {
            "gate_id": gate_id,
            "status": status,
            "value": value,
            "threshold": threshold,
            "reason": reason,
            "evidence_refs": list(evidence_refs),
            "evaluator": dict(evaluator),
            "evaluated_at": evaluated_at,
        }

    @event("WaiverRecorded")
    def record_waiver(
        self,
        tenant_id: UUID,
        release_id: UUID,
        gate_id: str,
        justification: str,
        approver: str,
        recorded_at: str,
    ) -> None:
        """Waive one gate on a draft; justification and approver are required."""
        if not justification.strip():
            raise ReleaseStateError("waiver justification must be non-empty")
        if not approver.strip():
            raise ReleaseStateError("waiver approver must be non-empty")
        release = self._release(tenant_id, release_id)
        if release["state"] != RELEASE_DRAFT:
            raise ReleaseStateError(
                f"release {release_id} is {release['state']}; waivers attach to drafts only"
            )
        self._release_map(release, "waivers")[gate_id] = {
            "gate_id": gate_id,
            "justification": justification,
            "approver": approver,
            "created_at": recorded_at,
        }

    @event("CodebookReleased")
    def release(self, tenant_id: UUID, release_id: UUID, approver: str, released_at: str) -> None:
        """draft_candidate -> released. Released rows are immutable afterwards."""
        if not approver.strip():
            raise ReleaseStateError("release approver must be non-empty")
        rec = self._release(tenant_id, release_id)
        if rec["state"] != RELEASE_DRAFT:
            raise ReleaseStateError(f"release {release_id} is {rec['state']}, not draft_candidate")
        rec["state"] = RELEASE_RELEASED
        rec["approver"] = approver
        rec["released_at"] = released_at

    @event("CodebookSuperseded")
    def supersede(
        self,
        tenant_id: UUID,
        release_id: UUID,
        superseded_by_release_id: UUID,
        actor: str,
        superseded_at: str,
    ) -> None:
        """released -> superseded (a state transition, never an edit)."""
        rec = self._release(tenant_id, release_id)
        if rec["state"] != RELEASE_RELEASED:
            raise ReleaseStateError(
                f"only released releases can be superseded; {release_id} is {rec['state']}"
            )
        rec["state"] = RELEASE_SUPERSEDED
        rec["superseded_by_release_id"] = str(superseded_by_release_id)
        rec["superseded_at"] = superseded_at
        rec["superseded_by_actor"] = actor

    @event("CodebookRetired")
    def retire(self, tenant_id: UUID, release_id: UUID, actor: str, retired_at: str) -> None:
        """released -> retired (withdrawn without a successor)."""
        rec = self._release(tenant_id, release_id)
        if rec["state"] != RELEASE_RELEASED:
            raise ReleaseStateError(
                f"only released releases can be retired; {release_id} is {rec['state']}"
            )
        rec["state"] = RELEASE_RETIRED
        rec["retired_at"] = retired_at
        rec["retired_by_actor"] = actor

    @event("CodebookChangeProposed")
    def propose_change(
        self,
        tenant_id: UUID,
        release_id: UUID,
        successor_release_id: UUID,
        summary: str,
        proposed_by: str,
        proposed_at: str,
        successor_manifest_json: dict[str, object],
        successor_manifest_sha256: str,
    ) -> None:
        """U25: propose a change against a released parent; the successor is a
        fresh draft_candidate linked to it. Never mutates the parent."""
        rec = self._release(tenant_id, release_id)
        if rec["state"] != RELEASE_RELEASED:
            raise ReleaseStateError(
                f"changes are proposed against released releases; {release_id} is {rec['state']}"
            )
        codebook_id = UUID(str(rec["codebook_id"]))
        self.request_release(
            tenant_id=tenant_id,
            release_id=successor_release_id,
            codebook_id=codebook_id,
            profile=str(rec["profile"]),
            creator=proposed_by,
            manifest_json=successor_manifest_json,
            manifest_sha256=successor_manifest_sha256,
            requested_at=proposed_at,
            parent_release_id=release_id,
        )
        prior = rec.get("change_proposals")
        existing = (
            [item for item in prior if isinstance(item, dict)] if isinstance(prior, list) else []
        )
        rec["change_proposals"] = [
            *existing,
            {
                "successor_release_id": str(successor_release_id),
                "summary": summary,
                "proposed_by": proposed_by,
                "proposed_at": proposed_at,
            },
        ]

    @event("ReviewerRubricRecorded")
    def record_rubric_review(
        self,
        tenant_id: UUID,
        proposal_id: str,
        reviewer: str,
        verdict: str,
        note: str,
        recorded_at: str,
    ) -> None:
        """U48 reviewer rubric over one proposed code (agreement |
        reasonable_alternative | not_reasonable); Wave 2 wires review_surface."""
        if verdict not in ("agreement", "reasonable_alternative", "not_reasonable"):
            raise ReleaseStateError(f"invalid rubric verdict: {verdict!r}")
        if not reviewer.strip():
            raise ReleaseStateError("rubric reviewer must be non-empty")
        self.rubric_reviews[str(proposal_id)] = {
            "tenant_id": str(tenant_id),
            "proposal_id": str(proposal_id),
            "reviewer": reviewer,
            "verdict": verdict,
            "note": note,
            "recorded_at": recorded_at,
        }
