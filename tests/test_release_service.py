"""Release lifecycle service (P1) — commands, enforcement, replay, compare."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from corpus_kb.domain.codebook import CodebookVersion, ReleaseStateError
from corpus_kb.research.release.compare import compare_releases
from corpus_kb.research.release.events import EVENT_NAMES, release_event
from corpus_kb.research.release.gates import GateInputs, GateResult
from corpus_kb.research.release.manifest import ReleaseManifest
from corpus_kb.research.release.profiles import (
    PROFILE_EXPLORATORY,
    PROFILE_HIGH_ASSURANCE,
    PROFILE_TEAM_CODEBOOK,
    PROFILES,
    ResolvedProfile,
    resolve_profile,
)
from corpus_kb.research.release.service import ReleaseService

TENANT = UUID("00000000-0000-0000-0000-000000000001")


class MemorySink:
    """In-memory EventSink (records every save for replay assertions)."""

    def __init__(self) -> None:
        self.saved: list[CodebookVersion] = []

    def save(self, aggregate: CodebookVersion) -> None:
        self.saved.append(aggregate)


def _aggregate() -> CodebookVersion:
    return CodebookVersion(tenant_id=TENANT, label="v1-test", sha256="c" * 64, paradigm="inductive")


def _manifest(version_tag: str = "1") -> ReleaseManifest:
    return ReleaseManifest(
        release_id=str(uuid4()),
        codebook_id=str(uuid4()),
        codebook_version=str(uuid4()),
        codebook_sha256="d" * 64,
        profile={"name": PROFILE_TEAM_CODEBOOK},
        method_declaration={"inductive": "hdbscan"},
        machine_profile={"model": "qwen3:8b", "seed": 42},
        created_at="2026-10-09T00:00:00+00:00",
        codes=[{"name": "cost", "definition": f"price concern v{version_tag}"}],
        metric_snapshots={"alpha": 0.8},
    )


def _pass(gate_id: str) -> GateResult:
    return GateResult(gate_id=gate_id, status="pass", reason="ok")


def _open_draft(
    profile: str = PROFILE_TEAM_CODEBOOK,
) -> tuple[ReleaseService, CodebookVersion, UUID, UUID, MemorySink]:
    sink = MemorySink()
    service = ReleaseService(sink)
    aggregate = _aggregate()
    release_id, codebook_id = uuid4(), uuid4()
    service.request_release(
        aggregate,
        release_id=release_id,
        codebook_id=codebook_id,
        profile_name=profile,
        creator="alice",
        manifest=_manifest(),
        requested_at="2026-10-09T00:00:00+00:00",
    )
    return service, aggregate, release_id, codebook_id, sink


def _record(aggregate: CodebookVersion, release_id: UUID) -> dict[str, object]:
    record = aggregate.releases[str(release_id)]
    assert isinstance(record, dict)
    return record


def test_request_release_is_idempotent_by_id_and_hashes_manifest():
    _service, aggregate, release_id, _, _ = _open_draft()
    record = _record(aggregate, release_id)
    assert record["state"] == "draft_candidate"
    assert record["creator"] == "alice"
    assert record["manifest_sha256"]
    with pytest.raises(ReleaseStateError, match="already exists"):
        aggregate.request_release(
            tenant_id=TENANT,
            release_id=release_id,
            codebook_id=uuid4(),
            profile=PROFILE_TEAM_CODEBOOK,
            creator="bob",
            manifest_json={},
            manifest_sha256="x",
            requested_at="now",
        )


def test_full_lifecycle_release_with_all_required_gates_passing():
    service, aggregate, release_id, _, sink = _open_draft()
    required = resolve_profile(str(_record(aggregate, release_id)["profile"])).required_gates
    for gate_id in required:
        assert (
            service.record_gate_result(
                aggregate, release_id, _pass(gate_id), evaluated_at="2026-10-09T00:01:00+00:00"
            )["status"]
            == "recorded"
        )
    outcome = service.release(
        aggregate, release_id, approver="dana", released_at="2026-10-09T00:02:00+00:00"
    )
    assert outcome["status"] == "released"
    assert outcome["unmet_required_gates"] == []
    assert _record(aggregate, release_id)["approver"] == "dana"
    assert len(sink.saved) == len(required) + 2


def test_release_requires_a_nonempty_approver():
    service, aggregate, release_id, _, _ = _open_draft(PROFILE_EXPLORATORY)
    with pytest.raises(ReleaseStateError, match="approver"):
        service.release(aggregate, release_id, approver="   ", released_at="now")


def test_enforce_blocks_unmet_required_gates_warn_releases_with_warnings():
    service, aggregate, release_id, _, _ = _open_draft(PROFILE_HIGH_ASSURANCE)
    # Default enforcement is WARN: unmet required gates release WITH warnings.
    warned = service.release(
        aggregate, release_id, approver="dana", released_at="2026-10-09T00:02:00+00:00"
    )
    assert warned["status"] == "released"
    assert warned["unmet_required_gates"]
    assert warned["warnings"]
    assert _record(aggregate, release_id)["state"] == "released"

    # ENFORCE blocks the same situation on a fresh draft.
    strict_name = "high-assurance-enforced"
    PROFILES[strict_name] = replace(
        PROFILES[PROFILE_HIGH_ASSURANCE], name=strict_name, enforcement="enforce"
    )
    service2, aggregate2, release2, _, _ = _open_draft(PROFILE_EXPLORATORY)
    _record(aggregate2, release2)["profile"] = strict_name
    try:
        outcome = service2.release(aggregate2, release2, approver="dana", released_at="t")
        assert outcome["status"] == "blocked"
        assert outcome["enforcement"] == "enforce"
        assert outcome["unmet_required_gates"]
        assert _record(aggregate2, release2)["state"] == "draft_candidate"
    finally:
        del PROFILES[strict_name]


def test_waiver_satisfies_a_required_gate_but_empty_fields_raise():
    service, aggregate, release_id, _, _ = _open_draft(PROFILE_HIGH_ASSURANCE)
    with pytest.raises(ReleaseStateError, match="justification"):
        service.record_waiver(aggregate, release_id, "audit_sample_design", "  ", "dana", "t")
    with pytest.raises(ReleaseStateError, match="approver"):
        service.record_waiver(aggregate, release_id, "audit_sample_design", "reason", "", "t")
    service.record_waiver(
        aggregate,
        release_id,
        "audit_sample_design",
        "schedule: audit lands next sprint",
        "dana",
        "t",
    )
    assert "audit_sample_design" in _record(aggregate, release_id)["waivers"]


def test_gates_and_waivers_attach_to_drafts_only():
    service, aggregate, release_id, _, _ = _open_draft(PROFILE_EXPLORATORY)
    service.release(aggregate, release_id, approver="dana", released_at="t")
    with pytest.raises(ReleaseStateError, match="drafts only"):
        service.record_gate_result(
            aggregate, release_id, _pass("machine_profile"), evaluated_at="t"
        )
    with pytest.raises(ReleaseStateError, match="drafts only"):
        service.record_waiver(aggregate, release_id, "machine_profile", "because", "dana", "t")


def test_supersede_and_retire_only_from_released():
    service, aggregate, release_id, _, _ = _open_draft(PROFILE_EXPLORATORY)
    with pytest.raises(ReleaseStateError, match="only released"):
        service.supersede(aggregate, release_id, uuid4(), actor="eve", superseded_at="t")
    service.release(aggregate, release_id, approver="dana", released_at="t")
    successor = uuid4()
    service.supersede(aggregate, release_id, successor, actor="eve", superseded_at="t2")
    record = _record(aggregate, release_id)
    assert record["state"] == "superseded"
    assert record["superseded_by_release_id"] == str(successor)
    with pytest.raises(ReleaseStateError, match="only released"):
        service.retire(aggregate, release_id, actor="eve", retired_at="t3")


def test_retire_withdraws_without_a_successor():
    service, aggregate, release_id, _, _ = _open_draft(PROFILE_EXPLORATORY)
    service.release(aggregate, release_id, approver="dana", released_at="t")
    outcome = service.retire(aggregate, release_id, actor="eve", retired_at="t2")
    assert outcome["status"] == "retired"
    assert _record(aggregate, release_id)["state"] == "retired"


def test_propose_change_creates_successor_draft_linked_to_parent():
    service, aggregate, release_id, codebook_id, _ = _open_draft(PROFILE_EXPLORATORY)
    service.release(aggregate, release_id, approver="dana", released_at="t")
    successor = uuid4()
    outcome = service.propose_change(
        aggregate,
        release_id,
        successor_release_id=successor,
        summary="split the cost code",
        proposed_by="frank",
        proposed_at="t3",
        successor_manifest=_manifest("2"),
    )
    assert outcome["status"] == "change_proposed"
    parent = _record(aggregate, release_id)
    successor_record = _record(aggregate, successor)
    assert parent["state"] == "released"  # the parent is never mutated
    assert successor_record["state"] == "draft_candidate"
    assert successor_record["parent_release_id"] == str(release_id)
    assert successor_record["codebook_id"] == str(codebook_id)


def test_propose_change_path_replays_from_persisted_events():
    """Finding 1 regression: the change-proposal path persists exactly ONE
    CodebookChangeProposed event. The nested ``request_release`` @event call
    used to double-append a CodebookReleaseRequested at the SAME
    originator_version, so real saves hit IntegrityError and replay raised
    OriginatorVersionError."""
    service, aggregate, release_id, _, sink = _open_draft(PROFILE_EXPLORATORY)
    service.release(aggregate, release_id, approver="dana", released_at="t")
    successor = uuid4()
    service.propose_change(
        aggregate,
        release_id,
        successor_release_id=successor,
        summary="split the cost code",
        proposed_by="frank",
        proposed_at="t3",
        successor_manifest=_manifest("2"),
    )
    events = list(aggregate.pending_events)
    names = [type(event).__name__ for event in events]
    assert names.count("CodebookReleaseRequested") == 1  # no nested duplicate
    assert names.count("CodebookChangeProposed") == 1
    versions = [event.originator_version for event in events]
    assert versions == list(range(1, len(events) + 1))  # no duplicate version

    replayed = events[0].mutate(None)
    for event in events[1:]:
        event.mutate(replayed)  # raised OriginatorVersionError before the fix
    assert replayed.releases == aggregate.releases
    successor_record = replayed.releases[str(successor)]
    assert successor_record["state"] == "draft_candidate"
    assert successor_record["parent_release_id"] == str(release_id)
    assert replayed.releases[str(release_id)]["state"] == "released"
    assert replayed.pending_events == []  # replay must not spawn new events
    assert len(sink.saved) == 3


def test_release_request_event_carries_project_and_version_hash():
    """Finding 8: project_id and codebook_version_sha256 ride on the persisted
    event (the projector reads them for codebook_releases) and land on the
    aggregate record."""
    project_id = uuid4()
    service = ReleaseService(MemorySink())
    aggregate = _aggregate()
    release_id = uuid4()
    manifest = _manifest()
    service.request_release(
        aggregate,
        release_id=release_id,
        codebook_id=uuid4(),
        profile_name=PROFILE_TEAM_CODEBOOK,
        creator="alice",
        manifest=manifest,
        requested_at="t1",
        project_id=project_id,
    )
    request_event = next(
        event
        for event in aggregate.pending_events
        if type(event).__name__ == "CodebookReleaseRequested"
    )
    assert str(request_event.project_id) == str(project_id)
    assert request_event.codebook_version_sha256 == manifest.codebook_sha256
    record = _record(aggregate, release_id)
    assert record["project_id"] == str(project_id)
    assert record["codebook_version_sha256"] == manifest.codebook_sha256


def test_events_replay_to_identical_state_and_manifest_hash():
    service, aggregate, release_id, _, sink = _open_draft(PROFILE_HIGH_ASSURANCE)
    service.record_gate_result(
        aggregate,
        release_id,
        GateResult(
            gate_id="clustering_determinism",
            status="pass",
            reason="proof",
            value={"matrix": "a" * 64},
        ),
        evaluated_at="t",
        evaluator={"gate_fn": "gate_clustering_determinism"},
    )
    service.record_waiver(aggregate, release_id, "audit_sample_design", "later", "dana", "t")
    service.release(aggregate, release_id, approver="dana", released_at="t")
    events = aggregate.pending_events
    replayed = events[0].mutate(None)
    for event in events[1:]:
        event.mutate(replayed)
    assert replayed.releases == aggregate.releases
    assert (
        replayed.releases[str(release_id)]["manifest_sha256"]
        == aggregate.releases[str(release_id)]["manifest_sha256"]
    )
    assert len(sink.saved) == 4


def test_assess_writes_nothing():
    sink = MemorySink()
    service = ReleaseService(sink)
    bundle = service.assess(GateInputs(), PROFILE_EXPLORATORY)
    assert bundle["enforcement"] == "warn"
    assert sink.saved == []


def test_compare_releases_diff_kinds():
    old = _manifest("1")
    new = _manifest("2")
    diff = compare_releases(old, new)
    assert diff.identical is False
    assert diff.codes_added == []  # same name, changed definition
    assert diff.definitions_changed == [{"code": "cost", "definition": "price concern v2"}]
    assert diff.metric_deltas == {}
    variant = _variant_manifest(metric_snapshots={"alpha": 0.9})
    diff_metrics = compare_releases(old, variant)
    assert diff_metrics.metric_deltas["alpha"] == pytest.approx(0.1)


def _variant_manifest(**overrides: object) -> ReleaseManifest:
    """A manifest sharing identity with _manifest('3') but with new blocks."""
    base = _manifest("3")
    payload = base.to_payload()
    timestamps = payload["timestamps"]
    assert isinstance(timestamps, dict)
    return ReleaseManifest(
        release_id=str(payload["release_id"]),
        codebook_id=str(payload["codebook_id"]),
        codebook_version=str(payload["codebook_version"]),
        codebook_sha256=str(payload["codebook_sha256"]),
        profile=dict(payload["profile"]),
        method_declaration=dict(payload["method_declaration"]),
        machine_profile=dict(payload["machine_profile"]),
        created_at=str(timestamps.get("created_at", "")),
        codes=[dict(c) for c in payload["codes"]],
        metric_snapshots=dict(overrides.get("metric_snapshots", {})),
    )


def test_compare_releases_detects_merged_renames():
    old = ReleaseManifest(
        release_id="1" * 8 + "-0000-0000-0000-000000000000",
        codebook_id="2" * 8 + "-0000-0000-0000-000000000000",
        codebook_version="3" * 8 + "-0000-0000-0000-000000000000",
        codebook_sha256="d" * 64,
        profile={},
        method_declaration={},
        machine_profile={},
        created_at="t",
        codes=[{"name": "cost", "definition": "same definition"}],
    )
    new = ReleaseManifest(
        release_id="4" * 8 + "-0000-0000-0000-000000000000",
        codebook_id=str(old.codebook_id),
        codebook_version="5" * 8 + "-0000-0000-0000-000000000000",
        codebook_sha256="e" * 64,
        profile={},
        method_declaration={},
        machine_profile={},
        created_at="t2",
        codes=[{"name": "price", "definition": "same definition"}],
    )
    diff = compare_releases(old, new)
    assert diff.codes_merged == [{"from": "cost", "to": "price"}]
    assert diff.codes_added == [] and diff.codes_removed == []


def test_release_event_schema_builder_matches_domain_names():
    event = release_event(
        "CodebookReleased",
        tenant_id=str(TENANT),
        release_id=str(uuid4()),
        approver="dana",
        released_at="t",
    )
    assert event["event"] in EVENT_NAMES
    with pytest.raises(ValueError, match="schema violation"):
        release_event("CodebookReleased", tenant_id=str(TENANT))


def test_rubric_verdicts_are_constrained():
    _service, aggregate, _, _, _ = _open_draft(PROFILE_EXPLORATORY)
    aggregate.record_rubric_review(
        tenant_id=TENANT,
        proposal_id="p1",
        reviewer="dana",
        verdict="agreement",
        note="matches",
        recorded_at="t",
    )
    assert aggregate.rubric_reviews["p1"]["verdict"] == "agreement"
    with pytest.raises(ReleaseStateError, match="verdict"):
        aggregate.record_rubric_review(
            tenant_id=TENANT,
            proposal_id="p2",
            reviewer="dana",
            verdict=" vibes",
            note="",
            recorded_at="t",
        )


def test_resolved_profile_is_frozen():
    profile = ResolvedProfile(name="x")
    with pytest.raises(AttributeError):
        profile.name = "y"
    assert set(PROFILES) == {
        PROFILE_EXPLORATORY,
        PROFILE_TEAM_CODEBOOK,
        PROFILE_HIGH_ASSURANCE,
    }
