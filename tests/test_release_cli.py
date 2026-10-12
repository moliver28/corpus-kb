"""U5/P7 release-surface tests — handlers, payload honesty, CLI exit codes.

OFFLINE by construction: the aggregate store is faked (``MemoryApp``), the
evidence reads are faked (``gate_inputs_for_version``), and the CLI pool is
faked — no Postgres, no Ollama. The lifecycle semantics themselves are
covered by tests/test_release_service.py against the real service; this
suite pins the SURFACE layer: one shared handler per operation, typed
payloads, guide_copy literal next actions, and honest exit codes (assess
never fails on not_evaluable; a blocked release exits 1).
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest
from typer.testing import CliRunner

from corpus_kb import cli
from corpus_kb.cli_release import _waiver_pairs
from corpus_kb.domain.codebook import CodebookVersion
from corpus_kb.handlers import release_handlers as rh
from corpus_kb.research import guide_copy
from corpus_kb.research.release.gate_types import GateInputs
from corpus_kb.research.release.gates import GateResult
from corpus_kb.research.release.manifest import ReleaseManifest
from corpus_kb.research.release.profiles import (
    PROFILE_EXPLORATORY,
    PROFILE_TEAM_CODEBOOK,
)
from corpus_kb.research.release.service import ReleaseService

TENANT = UUID("00000000-0000-0000-0000-000000000001")

FAKE_SUFFICIENCY = {"codebook": True, "partitions": False, "audit_sample": False}


class MemoryApp:
    """The eventsourcing application fake: repository.get + save (sink)."""

    def __init__(self, aggregate: CodebookVersion) -> None:
        self._aggregate = aggregate
        self.saved: list[CodebookVersion] = []

    def save(self, aggregate: CodebookVersion) -> None:
        self.saved.append(aggregate)

    @property
    def repository(self) -> MemoryApp:
        return self

    def get(self, _aggregate_id: UUID) -> CodebookVersion:
        return self._aggregate


class FakePool:
    async def close(self) -> None:  # the only member the CLI coroutines use
        pass


def _aggregate() -> CodebookVersion:
    return CodebookVersion(tenant_id=TENANT, label="v1-test", sha256="c" * 64)


def _wire(monkeypatch: pytest.MonkeyPatch, aggregate: CodebookVersion) -> MemoryApp:
    """Fake the aggregate store + evidence reads (offline seams)."""
    app = MemoryApp(aggregate)
    monkeypatch.setattr(rh, "_conn_str", lambda: "postgresql://fake")
    monkeypatch.setattr(rh, "_load_aggregate", lambda conn_str, vid: aggregate)
    monkeypatch.setattr(rh, "_app_sink", lambda: app)

    async def fake_inputs(pool: object, tenant_id: UUID, version_id: UUID, run_id: object = None):
        return GateInputs(), dict(FAKE_SUFFICIENCY)

    monkeypatch.setattr(rh, "gate_inputs_for_version", fake_inputs)
    return app


def _record(aggregate: CodebookVersion, release_id: UUID) -> dict[str, object]:
    return aggregate.releases[str(release_id)]


def _manifest(codes_version: str = "1", release_id: UUID | None = None) -> ReleaseManifest:
    return ReleaseManifest(
        release_id=str(release_id or uuid4()),
        codebook_id=str(uuid4()),
        codebook_version=str(uuid4()),
        codebook_sha256="d" * 64,
        profile={"name": PROFILE_TEAM_CODEBOOK},
        method_declaration={"inductive": "hdbscan"},
        machine_profile={"model": "qwen3:8b", "seed": 42},
        created_at="2026-10-12T00:00:00+00:00",
        codes=[{"name": "cost", "definition": f"price concern v{codes_version}"}],
        metric_snapshots={"alpha": 0.8},
    )


def _open_draft(
    profile: str = PROFILE_TEAM_CODEBOOK,
    manifest: ReleaseManifest | None = None,
) -> tuple[CodebookVersion, UUID, MemoryApp]:
    aggregate = _aggregate()
    app = MemoryApp(aggregate)
    release_id = uuid4()
    ReleaseService(app).request_release(
        aggregate,
        release_id=release_id,
        codebook_id=uuid4(),
        profile_name=profile,
        creator="alice",
        manifest=manifest or _manifest(release_id=release_id),
        requested_at="2026-10-12T00:00:00+00:00",
    )
    return aggregate, release_id, app


# ---------------------------------------------------------------------------
# Pure stage derivation + next actions (D1)
# ---------------------------------------------------------------------------


def test_release_stage_derivation_is_honest() -> None:
    assert rh.release_stage(None) == rh.STAGE_PRE_DRAFT
    team_draft: dict[str, object] = {
        "state": "draft_candidate",
        "profile": PROFILE_TEAM_CODEBOOK,
        "gates": {},
        "waivers": {},
    }
    assert rh.release_stage(team_draft) == rh.STAGE_GATE_EVIDENCE
    ready: dict[str, object] = {
        "state": "draft_candidate",
        "profile": PROFILE_EXPLORATORY,
        "gates": {},
        "waivers": {},
    }
    assert rh.release_stage(ready) == rh.STAGE_READY_FOR_RELEASE
    assert rh.release_stage({"state": "released", "profile": PROFILE_EXPLORATORY}) == (
        rh.STAGE_RELEASED
    )
    assert rh.release_stage({"state": "superseded", "profile": PROFILE_EXPLORATORY}) == (
        rh.STAGE_CLOSED
    )


def test_recommended_next_action_is_always_a_literal_command() -> None:
    for stage in (
        rh.STAGE_PRE_DRAFT,
        rh.STAGE_GATE_EVIDENCE,
        rh.STAGE_READY_FOR_RELEASE,
        rh.STAGE_RELEASED,
        rh.STAGE_CLOSED,
    ):
        action = rh.recommended_next_action(stage)
        assert action.startswith("corpus-kb release "), stage
        assert "<" in action  # the caller fills the placeholders
    assert rh.recommended_next_action(rh.STAGE_PRE_DRAFT) == guide_copy.RELEASE_NEXT_REQUEST
    assert rh.recommended_next_action(rh.STAGE_GATE_EVIDENCE) == guide_copy.RELEASE_NEXT_ASSESS


def test_unmet_required_gates_respects_passes_and_waivers() -> None:
    record: dict[str, object] = {
        "profile": PROFILE_TEAM_CODEBOOK,
        "gates": {"machine_profile": {"status": "pass"}},
        "waivers": {"definition_completeness": {"approver": "dana"}},
    }
    unmet = rh.unmet_required_gates(record)
    assert "machine_profile" not in unmet
    assert "definition_completeness" not in unmet
    assert "reliability_alpha" in unmet


# ---------------------------------------------------------------------------
# request / assess handlers
# ---------------------------------------------------------------------------


def test_request_release_opens_draft_and_hashes_the_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aggregate = _aggregate()
    aggregate.add_codes(
        TENANT,
        [{"code_id": "c1", "name": "cost", "definition": "price concern"}],
    )
    app = _wire(monkeypatch, aggregate)
    payload = _run(rh.request_release(None, TENANT, aggregate.id, "alice"))
    assert payload.status == "requested"
    record = _record(aggregate, UUID(payload.release_id))
    assert record["state"] == "draft_candidate"
    assert payload.manifest_sha256 == record["manifest_sha256"]
    assert app.saved, "the draft event must be persisted"
    codes_block = _manifest_from_record(record)["codes"]
    assert codes_block == [{"name": "cost", "definition": "price concern"}]


def _manifest_from_record(record: dict[str, object]) -> dict[str, object]:
    manifest = record.get("manifest_json")
    assert isinstance(manifest, dict)
    return manifest


def test_request_release_rejects_foreign_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    aggregate = _aggregate()
    _wire(monkeypatch, aggregate)
    stranger = UUID("00000000-0000-0000-0000-000000009999")
    with pytest.raises(ValueError, match="tenant"):
        _run(rh.request_release(None, stranger, aggregate.id, "alice"))


def test_assess_reports_not_evaluable_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aggregate = _aggregate()
    app = _wire(monkeypatch, aggregate)
    payload = _run(rh.assess_release(None, TENANT, aggregate.id))
    assert payload.to_payload()["status"] == "assessed"
    assert payload.profile == PROFILE_TEAM_CODEBOOK  # the config default profile
    assert payload.required_not_evaluable, "missing evidence must be reported"
    assert payload.all_required_pass_without_waivers is False
    assert payload.data_sufficiency == FAKE_SUFFICIENCY
    assert app.saved == [], "assess WRITES NOTHING"
    gate_ids = {str(gate.get("gate_id")) for gate in payload.gate_results}
    assert len(gate_ids) == 19  # every registered gate reports honestly


# ---------------------------------------------------------------------------
# review packet + execute + status + compare handlers
# ---------------------------------------------------------------------------


def test_review_packet_lists_failures_waivers_and_next_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aggregate, release_id, _app = _open_draft()
    service = ReleaseService(MemoryApp(aggregate))
    service.record_gate_result(
        aggregate,
        release_id,
        GateResult(gate_id="machine_profile", status="fail", reason="nope"),
        evaluated_at="t",
    )
    service.record_gate_result(
        aggregate,
        release_id,
        GateResult(gate_id="reliability_alpha", status="not_evaluable", reason="no data"),
        evaluated_at="t",
    )
    service.record_waiver(aggregate, release_id, "definition_completeness", "later", "dana", "t")
    _wire(monkeypatch, aggregate)
    packet = _run(rh.review_release(None, TENANT, aggregate.id, release_id))
    assert packet.release_state == "draft_candidate"
    assert packet.failing_gates == ["machine_profile"]
    assert packet.not_evaluable_gates == ["reliability_alpha"]
    assert packet.waived_gates == ["definition_completeness"]
    assert "machine_profile" in packet.unmet_required_gates
    assert packet.next_actions, "the packet must name the SPECIFIC next actions"
    assert packet.recommended_next_action == packet.next_actions[0]
    assert packet.recommended_next_action.startswith("corpus-kb release assess")
    statuses = {str(gate.get("gate_id")): str(gate.get("status")) for gate in packet.evidence}
    assert statuses == {"machine_profile": "fail", "reliability_alpha": "not_evaluable"}


def test_review_packet_for_missing_release_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    aggregate, _release_id, _app = _open_draft(PROFILE_EXPLORATORY)
    _wire(monkeypatch, aggregate)
    with pytest.raises(ValueError, match="not found"):
        _run(rh.review_release(None, TENANT, aggregate.id, uuid4()))


def test_execute_release_records_gates_and_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aggregate, release_id, app = _open_draft(PROFILE_EXPLORATORY)
    _wire(monkeypatch, aggregate)
    outcome = _run(rh.execute_release(None, TENANT, aggregate.id, release_id, "dana"))
    assert outcome.status == "released"
    assert outcome.unmet_required_gates == []
    record = _record(aggregate, release_id)
    assert record["state"] == "released"
    assert record["approver"] == "dana"
    gates = _record(aggregate, release_id)["gates"]
    assert isinstance(gates, dict) and gates, "evaluated gate results must be recorded"
    assert app.saved, "gate + release events persisted"


def test_execute_release_blocked_under_enforce_keeps_the_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enforce_cfg: dict[str, object] = {
        "research": {"release": {"enforcement": "enforce", "default_profile": "team-codebook"}}
    }
    aggregate, release_id, _app = _open_draft(PROFILE_EXPLORATORY)
    _record(aggregate, release_id)["profile"] = "team-codebook"
    _wire(monkeypatch, aggregate)
    outcome = _run(
        rh.execute_release(None, TENANT, aggregate.id, release_id, "dana", config=enforce_cfg)
    )
    assert outcome.status == "blocked"
    assert outcome.unmet_required_gates
    assert _record(aggregate, release_id)["state"] == "draft_candidate"


def test_execute_release_waiver_param_lands_on_the_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aggregate, release_id, _app = _open_draft(PROFILE_TEAM_CODEBOOK)
    _wire(monkeypatch, aggregate)
    outcome = _run(
        rh.execute_release(
            None,
            TENANT,
            aggregate.id,
            release_id,
            "dana",
            waivers=(("machine_profile", "profile pinned in the repo README"),),
        )
    )
    assert outcome.status == "released"  # default enforcement is warn
    waivers = _record(aggregate, release_id)["waivers"]
    assert isinstance(waivers, dict) and "machine_profile" in waivers
    # The waiver satisfied machine_profile; the other six required gates are
    # still honestly unmet (and released WITH warnings under enforcement=warn).
    unmet = outcome.unmet_required_gates
    assert "machine_profile" not in unmet
    assert len(unmet) == 6
    assert outcome.warnings


def test_execute_release_rejects_unknown_waiver_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aggregate, release_id, _app = _open_draft(PROFILE_EXPLORATORY)
    _wire(monkeypatch, aggregate)
    with pytest.raises(ValueError, match="unknown release gate"):
        _run(
            rh.execute_release(
                None,
                TENANT,
                aggregate.id,
                release_id,
                "dana",
                waivers=(("not_a_gate", "because"),),
            )
        )


def test_status_reports_stage_blockers_and_literal_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aggregate, release_id, _app = _open_draft(PROFILE_TEAM_CODEBOOK)
    _wire(monkeypatch, aggregate)
    report = _run(rh.status_report(None, TENANT, aggregate.id))
    assert report.current_stage == rh.STAGE_GATE_EVIDENCE
    assert report.release_state == "draft_candidate"
    assert report.blocking_criteria, "unmet gates are the blocking criteria"
    assert all(": " in criterion for criterion in report.blocking_criteria)
    assert report.recommended_next_action == guide_copy.RELEASE_NEXT_ASSESS
    assert report.active_release is None
    draft = report.draft
    assert draft and draft.get("release_id") == str(release_id)
    assert report.pending_change_proposals == []
    assert report.data_sufficiency == FAKE_SUFFICIENCY


def test_status_released_stage_and_pending_proposals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aggregate, release_id, app = _open_draft(PROFILE_EXPLORATORY)
    ReleaseService(app).release(aggregate, release_id, approver="dana", released_at="t2")
    successor = uuid4()
    ReleaseService(app).propose_change(
        aggregate,
        release_id=release_id,
        successor_release_id=successor,
        summary="split the cost code",
        proposed_by="frank",
        proposed_at="t3",
        successor_manifest=_manifest("2"),
    )
    _wire(monkeypatch, aggregate)
    report = _run(rh.status_report(None, TENANT, aggregate.id))
    assert report.active_release is not None
    assert report.active_release.get("release_id") == str(release_id)
    assert report.release_state == "draft_candidate"  # the successor draft is primary
    assert report.current_stage == rh.STAGE_READY_FOR_RELEASE
    assert report.recommended_next_action == guide_copy.RELEASE_NEXT_RELEASE
    assert len(report.pending_change_proposals) == 1
    proposal = report.pending_change_proposals[0]
    assert proposal.get("parent_release_id") == str(release_id)


def test_compare_diffs_manifests_and_flags_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aggregate, release_id, app = _open_draft(PROFILE_EXPLORATORY)
    ReleaseService(app).release(aggregate, release_id, approver="dana", released_at="t2")
    successor = uuid4()
    ReleaseService(app).propose_change(
        aggregate,
        release_id=release_id,
        successor_release_id=successor,
        summary="split the cost code",
        proposed_by="frank",
        proposed_at="t3",
        successor_manifest=_manifest("2"),
    )
    _wire(monkeypatch, aggregate)
    result = _run(rh.compare_version_releases(TENANT, aggregate.id, release_id, successor))
    assert result.identical is False
    assert result.diff["definitions_changed"] == [
        {"code": "cost", "definition": "price concern v2"}
    ]
    same = _run(rh.compare_version_releases(TENANT, aggregate.id, release_id, release_id))
    assert same.identical is True


def test_compare_missing_release_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    aggregate, release_id, _app = _open_draft(PROFILE_EXPLORATORY)
    _wire(monkeypatch, aggregate)
    with pytest.raises(ValueError, match="not found"):
        _run(rh.compare_version_releases(TENANT, aggregate.id, release_id, uuid4()))


# ---------------------------------------------------------------------------
# CLI: honest exit codes + --json payloads (D3/D4)
# ---------------------------------------------------------------------------


def _invoke(monkeypatch: pytest.MonkeyPatch, args: list[str]):
    async def fake_pool() -> FakePool:
        return FakePool()

    monkeypatch.setattr("corpus_kb.cli_release._pool_conn", fake_pool)
    return CliRunner().invoke(cli.app, args)


def test_cli_assess_json_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_assess(pool, tenant, version, profile_name=None, run_id=None):
        return rh.AssessResult(
            codebook_version_id=str(version),
            profile=PROFILE_TEAM_CODEBOOK,
            enforcement="warn",
            gate_results=[{"gate_id": "machine_profile", "status": "not_evaluable"}],
            required_gate_ids=["machine_profile"],
            required_failing=[],
            required_not_evaluable=["machine_profile"],
            all_required_pass_without_waivers=False,
            data_sufficiency={"codebook": False},
        )

    monkeypatch.setattr(rh, "assess_release", fake_assess)
    result = _invoke(
        monkeypatch,
        ["release", "assess", "--codebook-version-id", str(uuid4()), "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "assessed"
    assert payload["required_not_evaluable"] == ["machine_profile"]


def test_cli_release_blocked_exits_one_with_structured_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute(pool, tenant, version, release, approver, waivers=(), run_id=None):
        return rh.ReleaseOutcome(
            status="blocked",
            release_id=str(release),
            approver=approver,
            enforcement="enforce",
            unmet_required_gates=["reliability_alpha"],
            warnings=[],
            reason="required gates must be pass or waived before release",
        )

    monkeypatch.setattr(rh, "execute_release", fake_execute)
    result = _invoke(
        monkeypatch,
        [
            "release",
            "release",
            "--codebook-version-id",
            str(uuid4()),
            "--release-id",
            str(uuid4()),
            "--approver",
            "dana",
            "--json",
        ],
    )
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "blocked"
    assert payload["unmet_required_gates"] == ["reliability_alpha"]


def test_cli_release_success_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_execute(pool, tenant, version, release, approver, waivers=(), run_id=None):
        return rh.ReleaseOutcome(
            status="released",
            release_id=str(release),
            approver=approver,
            enforcement="warn",
            unmet_required_gates=[],
            warnings=["gate reliability_alpha unmet at release (enforcement=warn)"],
        )

    monkeypatch.setattr(rh, "execute_release", fake_execute)
    result = _invoke(
        monkeypatch,
        [
            "release",
            "release",
            "--codebook-version-id",
            str(uuid4()),
            "--release-id",
            str(uuid4()),
            "--approver",
            "dana",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "WARNING:" in result.output


def test_cli_status_renders_stage_and_next_command(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_status(pool, tenant, version):
        return rh.StatusReport(
            codebook_version_id=str(version),
            current_stage=rh.STAGE_PRE_DRAFT,
            release_state="none",
            blocking_criteria=[],
            recommended_next_action=guide_copy.RELEASE_NEXT_REQUEST,
            active_release=None,
            draft=None,
            pending_change_proposals=[],
            data_sufficiency={"codebook": False},
        )

    monkeypatch.setattr(rh, "status_report", fake_status)
    result = _invoke(monkeypatch, ["release", "status", "--codebook-version-id", str(uuid4())])
    assert result.exit_code == 0, result.output
    assert guide_copy.RELEASE_STAGE_LABELS[rh.STAGE_PRE_DRAFT] in result.output
    assert "corpus-kb release request" in result.output
    assert guide_copy.RELEASE_NO_RELEASE_NOTE in result.output


def test_cli_review_renders_the_packet(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_review(pool, tenant, version, release):
        return rh.ReviewPacket(
            release_id=str(release),
            release_state="draft_candidate",
            profile=PROFILE_TEAM_CODEBOOK,
            enforcement="warn",
            evidence=[{"gate_id": "machine_profile", "status": "fail", "reason": "nope"}],
            failing_gates=["machine_profile"],
            not_evaluable_gates=["reliability_alpha"],
            waived_gates=["definition_completeness"],
            unmet_required_gates=["machine_profile", "reliability_alpha"],
            next_actions=[guide_copy.RELEASE_NEXT_ASSESS],
            recommended_next_action=guide_copy.RELEASE_NEXT_ASSESS,
        )

    monkeypatch.setattr(rh, "review_release", fake_review)
    result = _invoke(
        monkeypatch,
        [
            "release",
            "review",
            "--codebook-version-id",
            str(uuid4()),
            "--release-id",
            str(uuid4()),
        ],
    )
    assert result.exit_code == 0, result.output
    assert guide_copy.RELEASE_EVIDENCE_HEADER in result.output
    assert guide_copy.RELEASE_WAIVERS_HEADER in result.output
    assert guide_copy.RELEASE_UNMET_HEADER in result.output
    assert "corpus-kb release assess" in result.output


def test_cli_compare_json_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_compare(tenant, version, old, new):
        return rh.CompareResult(
            old_release_id=str(old),
            new_release_id=str(new),
            diff={"identical": False, "codes_added": ["price"]},
        )

    monkeypatch.setattr(rh, "compare_version_releases", fake_compare)
    result = _invoke(
        monkeypatch,
        [
            "release",
            "compare",
            "--codebook-version-id",
            str(uuid4()),
            "--old-release-id",
            str(uuid4()),
            "--new-release-id",
            str(uuid4()),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "compared"
    assert payload["codes_added"] == ["price"]


def test_waiver_pairs_parse_gate_equals_justification() -> None:
    assert _waiver_pairs(["a=x", " b = y "]) == (("a", "x"), ("b", "y"))
    with pytest.raises(ValueError, match="GATE_ID=justification"):
        _waiver_pairs(["no-separator"])
    with pytest.raises(ValueError, match="GATE_ID=justification"):
        _waiver_pairs(["gate="])


# ---------------------------------------------------------------------------
# helper
# ---------------------------------------------------------------------------


def _run(coroutine: object):
    import asyncio
    from collections.abc import Coroutine
    from typing import Any, cast

    return asyncio.run(cast("Coroutine[Any, Any, object]", coroutine))
