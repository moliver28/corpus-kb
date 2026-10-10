"""U48 reviewer rubric + U41/U47 routing + U37 batching (offline).

Covers: the three-way rubric (record + aggregate), the risk-coverage
routing composition (auto-accept/audit vs review, insufficient-data
fail-safe), the config-driven review batch, the monitoring alarms ->
change-proposal path (U25), and the N-run consensus wiring reader (U48).
No Postgres, no Ollama.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from corpus_kb.coding.measurement_settings import (
    AuditSettings,
    MeasurementSettings,
    ReviewSettings,
    RoutingSettings,
)
from corpus_kb.domain.codebook import CodebookVersion, ReleaseStateError
from corpus_kb.research.cycle_gates import GateFinding
from corpus_kb.research.monitoring import (
    Alarm,
    alarms_block,
    alarms_from_findings,
    propose_from_alarms,
    successor_manifest,
)
from corpus_kb.research.release.manifest import ReleaseManifest
from corpus_kb.research.release.service import ReleaseService
from corpus_kb.research.review_routing import (
    REASON_INSUFFICIENT,
    ROUTE_AUTO_ACCEPT,
    ROUTE_REVIEW,
    route_for_review,
)
from corpus_kb.research.review_surface import (
    RUBRIC_VERDICTS,
    aggregate_rubric,
    validate_rubric_verdict,
)

TENANT = UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# U48 rubric: verdict validation + honest aggregation
# ---------------------------------------------------------------------------


def test_rubric_verdicts_are_the_wave1_three_way_set() -> None:
    assert RUBRIC_VERDICTS == ("agreement", "reasonable_alternative", "not_reasonable")
    assert validate_rubric_verdict("agreement") == "agreement"
    with pytest.raises(ValueError, match="rubric verdict"):
        validate_rubric_verdict("vibes")


def test_aggregate_rubric_counts_and_honest_empty() -> None:
    reviews = [
        {"verdict": "agreement", "reviewer": "dana"},
        {"verdict": "reasonable_alternative", "reviewer": "dana"},
        {"verdict": "agreement", "reviewer": "eve"},
    ]
    block = aggregate_rubric(reviews)
    assert block["n"] == 3
    assert block["counts"]["agreement"] == 2
    assert block["counts"]["reasonable_alternative"] == 1
    assert block["counts"]["not_reasonable"] == 0
    assert block["agreement_rate"] == pytest.approx(2 / 3)
    assert block["n_reviewers"] == 2

    empty = aggregate_rubric([])
    assert empty["n"] == 0
    assert empty["agreement_rate"] is None  # unknown, never 0
    assert empty["counts"] == {
        "agreement": 0,
        "reasonable_alternative": 0,
        "not_reasonable": 0,
    }


class _FakeRepository:
    def __init__(self, aggregate: CodebookVersion) -> None:
        self._aggregate = aggregate

    def get(self, _aggregate_id: UUID) -> CodebookVersion:
        return self._aggregate


class _FakeApp:
    def __init__(self, aggregate: CodebookVersion) -> None:
        self.repository = _FakeRepository(aggregate)
        self.saved: list[CodebookVersion] = []

    def save(self, aggregate: CodebookVersion) -> None:
        self.saved.append(aggregate)


async def test_execute_rubric_review_records_event_and_aggregates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from corpus_kb.research import review_surface

    aggregate = CodebookVersion(
        tenant_id=TENANT, label="v1-rubric", sha256="c" * 64, paradigm="inductive"
    )
    app = _FakeApp(aggregate)
    monkeypatch.setattr(review_surface, "_app", lambda _conn_str: app)

    result = await review_surface.execute_rubric_review(
        None,
        TENANT,
        "memory://",
        uuid4(),
        "proposal-1",
        "dana",
        "agreement",
        note="matches the reference codebook",
    )
    assert result["status"] == "success"
    assert result["verdict"] == "agreement"
    assert result["aggregation"]["n"] == 1
    assert len(app.saved) == 1
    assert aggregate.rubric_reviews["proposal-1"]["verdict"] == "agreement"

    with pytest.raises(ValueError, match="rubric verdict"):
        await review_surface.execute_rubric_review(
            None,
            TENANT,
            "memory://",
            uuid4(),
            "proposal-2",
            "dana",
            "maybe",
        )
    assert len(app.saved) == 1  # the invalid verdict mutated nothing


# ---------------------------------------------------------------------------
# U41/U47 routing: auto-accept + audit sample vs review, honest fail-safe
# ---------------------------------------------------------------------------


def _separable_data(n: int) -> tuple[list[float], list[float]]:
    """Signal = true correctness probability; correctness flips at 0.5.

    Deterministic and monotone: any threshold in (0.495, 0.5) separates the
    classes perfectly, so the CP-guarded threshold is well-defined.
    """
    signals = [0.05 + 0.9 * i / (n - 1) for i in range(n)]
    correct = [1.0 if p >= 0.5 else 0.0 for p in signals]
    return signals, correct


def _settings(
    *, target_risk: float = 0.1, min_calibration_n: int = 30, sample_rate: float = 0.5
) -> MeasurementSettings:
    return MeasurementSettings(
        routing=RoutingSettings(
            target_risk=target_risk, confidence_level=0.95, min_calibration_n=min_calibration_n
        ),
        audit=AuditSettings(sample_rate=sample_rate, seed=42),
        review=ReviewSettings(batch_size=4, random_fraction=0.5, diversity=False),
    )


def _plan_for(settings: MeasurementSettings) -> tuple[Any, dict[str, float]]:
    cal_signals, cal_correct = _separable_data(100)
    test_signals, test_correct = _separable_data(60)
    raw = [0.05 + 0.9 * i / 11 for i in range(12)]
    unit_scores = {f"u{i}": p for i, p in enumerate(raw)}
    unit_codes = {f"u{i}": "code-a" if i % 2 else "code-b" for i in range(12)}
    plan = route_for_review(
        {"sim": cal_signals},
        cal_correct,
        {"sim": test_signals},
        test_correct,
        unit_scores,
        unit_codes,
        settings,
    )
    return plan, unit_scores


def test_routing_separates_auto_accept_from_review_with_audit_sample() -> None:
    plan, unit_scores = _plan_for(_settings())
    assert plan.status == "ok"
    assert plan.threshold is not None
    assert plan.chosen_signal == "sim"
    assert plan.n_auto_accept + plan.n_review == len(unit_scores)
    for decision in plan.decisions:
        if decision.route == ROUTE_AUTO_ACCEPT:
            assert decision.score >= plan.threshold
        else:
            assert decision.route == ROUTE_REVIEW
            assert decision.reason == "below_routing_threshold"
            assert decision.score < plan.threshold
    # Monotone calibrated scores: every auto-accepted unit outscores every
    # reviewed one (the calibrator is monotone in the raw signal, which is
    # ascending in unit index).
    accepted_raw = [unit_scores[d.unit_id] for d in plan.decisions if d.route == ROUTE_AUTO_ACCEPT]
    review_raw = [unit_scores[d.unit_id] for d in plan.decisions if d.route == ROUTE_REVIEW]
    if accepted_raw and review_raw:
        assert min(accepted_raw) > max(review_raw)
    # Audit sample: half of the auto-accepted units, seeded + reproducible.
    expected_n = round(plan.n_auto_accept * 0.5)
    assert len(plan.audit_sample) == expected_n
    sampled = {row.unit_id for row in plan.audit_sample}
    accepted_ids = {d.unit_id for d in plan.decisions if d.route == ROUTE_AUTO_ACCEPT}
    assert sampled <= accepted_ids
    assert all(row.scope == "routing_auto_accept" for row in plan.audit_sample)
    replay, _ = _plan_for(_settings())
    assert {row.unit_id for row in replay.audit_sample} == sampled


def test_insufficient_calibration_routes_everything_to_review() -> None:
    plan, unit_scores = _plan_for(_settings(min_calibration_n=1000))
    assert plan.status == "insufficient_data"
    assert plan.threshold is None
    assert plan.audit_sample == ()
    assert plan.n_review == len(unit_scores)
    assert all(d.route == ROUTE_REVIEW for d in plan.decisions)
    assert all(d.reason == REASON_INSUFFICIENT for d in plan.decisions)


def test_single_class_calibration_routes_everything_to_review() -> None:
    cal_signals, _ = _separable_data(60)
    test_signals, test_correct = _separable_data(30)
    plan = route_for_review(
        {"sim": cal_signals},
        [1.0] * len(cal_signals),  # one class only: AUROC undefined
        {"sim": test_signals},
        test_correct,
        {"u0": 0.9, "u1": 0.2},
        {"u0": "c", "u1": "c"},
        _settings(),
    )
    assert plan.status == "insufficient_data"
    assert [d.route for d in plan.decisions] == [ROUTE_REVIEW, ROUTE_REVIEW]


def test_unattainable_target_risk_routes_everything_to_review() -> None:
    cal_signals, cal_correct = _separable_data(100)
    test_signals, test_correct = _separable_data(60)
    plan = route_for_review(
        {"sim": cal_signals},
        cal_correct,
        {"sim": test_signals},
        test_correct,
        {"u0": 0.9, "u1": 0.2},
        {"u0": "c", "u1": "c"},
        _settings(target_risk=0.0),  # CP bounds are strictly positive
    )
    assert plan.status == "insufficient_data"
    assert plan.threshold is None
    assert all(d.route == ROUTE_REVIEW for d in plan.decisions)


# ---------------------------------------------------------------------------
# U37 batching: config-read sizes through the wave-1 selection
# ---------------------------------------------------------------------------


def test_next_review_batch_uses_config_settings() -> None:
    from corpus_kb.research.review_routing import next_review_batch

    review = ReviewSettings(batch_size=6, random_fraction=0.5, diversity=False)
    units = [f"u{i}" for i in range(10)]
    confidences = {f"u{i}": i / 10 for i in range(10)}
    batch = next_review_batch(units, confidences, None, [], review, seed=7)
    assert len(batch.items) == 6
    assert batch.batch_size == 6
    assert batch.random_fraction == 0.5
    assert batch.diversity_applied is False
    strata = {item.stratum for item in batch.items}
    assert strata == {"active", "random"}
    assert batch.random_items
    again = next_review_batch(units, confidences, None, [], review, seed=7)
    assert [i.unit_id for i in batch.items] == [i.unit_id for i in again.items]


def test_measurement_settings_reader_reads_the_research_block() -> None:
    from corpus_kb.research.review_routing import measurement_settings_from_config

    settings = measurement_settings_from_config(
        {"research": {"routing": {"target_risk": 0.02}, "audit": {"sample_rate": 0.25}}}
    )
    assert settings.routing.target_risk == 0.02
    assert settings.audit.sample_rate == 0.25
    assert settings.review.batch_size == 20  # untouched default
    with pytest.raises(ValueError, match="target_risk"):
        measurement_settings_from_config({"research": {"routing": {"target_risk": "low"}}})
    with pytest.raises(ValueError, match="research"):
        measurement_settings_from_config({"research": 3})


# ---------------------------------------------------------------------------
# U25 monitoring: alarms -> change proposals, parent never mutated
# ---------------------------------------------------------------------------


def _parent_with_release() -> tuple[CodebookVersion, UUID, ReleaseManifest, _FakeApp]:
    aggregate = CodebookVersion(
        tenant_id=TENANT, label="v1-mon", sha256="d" * 64, paradigm="deductive"
    )
    sink = _FakeApp(aggregate)
    service = ReleaseService(sink)
    release_id = uuid4()
    manifest = ReleaseManifest(
        release_id=str(release_id),
        codebook_id=str(uuid4()),
        codebook_version=str(aggregate.id) if aggregate.id else str(uuid4()),
        codebook_sha256="e" * 64,
        profile={"name": "exploratory"},
        method_declaration={},
        machine_profile={"model": "qwen3:8b"},
        created_at="2026-10-09T00:00:00+00:00",
        codes=[{"name": "cost", "definition": "price concern"}],
    )
    service.request_release(
        aggregate,
        release_id=release_id,
        codebook_id=UUID(str(manifest.codebook_id)),
        profile_name="exploratory",
        creator="alice",
        manifest=manifest,
        requested_at="t",
    )
    service.release(aggregate, release_id, approver="dana", released_at="t2")
    return aggregate, release_id, manifest, sink


def test_alarms_map_only_the_monitored_gates() -> None:
    findings = [
        GateFinding("drift_alarm", "dbcv dropped", {"a": 1}),
        GateFinding("codebook_promotion", "proposals await", {}),
        GateFinding("interpretive_code_review", "by design", {}),
        GateFinding("threshold_unreliable", "thin gold", {"codes": ["c1"]}),
    ]
    alarms = alarms_from_findings(findings)
    kinds = {alarm.kind for alarm in alarms}
    assert kinds == {"drift", "reliability"}
    block = alarms_block(alarms)
    assert block["n_alarms"] == 2
    entries = block["alarms"]
    assert isinstance(entries, list)
    assert {str(entry["gate"]) for entry in entries} == {
        "drift_alarm",
        "threshold_unreliable",
    }


def test_successor_manifest_copies_codebook_and_attaches_alarms() -> None:
    _aggregate, release_id, manifest, _sink = _parent_with_release()
    alarms = [Alarm("drift_alarm", "drift", "dbcv dropped", {})]
    successor = successor_manifest(manifest, uuid4(), alarms, "2026-10-09T01:00:00+00:00")
    assert successor.codebook_sha256 == manifest.codebook_sha256
    assert successor.codes == manifest.codes  # monitoring never edits content
    assert successor.parent_release_id == str(release_id)
    assert "monitoring_alarms" in successor.metric_snapshots


def test_propose_from_alarms_links_successor_and_never_mutates_parent() -> None:
    aggregate, release_id, manifest, sink = _parent_with_release()
    parent_before = dict(aggregate.releases[str(release_id)])
    alarms = [
        Alarm("gray_zone_escalation", "routing", "queue pending", {"pending_reviews": 3}),
        Alarm("human_parity_breach", "calibration", "alpha below floor", {}),
    ]
    outcome = propose_from_alarms(
        aggregate,
        parent_release_id=release_id,
        parent_manifest=manifest,
        alarms=alarms,
        version_id=UUID(str(aggregate.id)),
        sink=sink,
        proposed_at="t3",
    )
    assert outcome["status"] == "change_proposed"
    successor_id = str(outcome["successor_release_id"])
    successor = aggregate.releases[successor_id]
    parent = aggregate.releases[str(release_id)]
    assert successor["state"] == "draft_candidate"
    assert successor["parent_release_id"] == str(release_id)
    assert parent["state"] == "released"  # never mutated
    assert parent["manifest_sha256"] == parent_before["manifest_sha256"]
    proposals = parent.get("change_proposals")
    assert isinstance(proposals, list) and proposals
    assert str(proposals[0]["summary"]).startswith("monitoring alarms")
    assert len(outcome["alarms"]) == 2

    no_alarms = propose_from_alarms(
        aggregate,
        parent_release_id=release_id,
        parent_manifest=manifest,
        alarms=[],
        version_id=UUID(str(aggregate.id)),
        sink=sink,
        proposed_at="t4",
    )
    assert no_alarms["status"] == "no_alarms"


def test_propose_requires_a_released_parent() -> None:
    aggregate = CodebookVersion(
        tenant_id=TENANT, label="v1-draft", sha256="f" * 64, paradigm="deductive"
    )
    sink = _FakeApp(aggregate)
    service = ReleaseService(sink)
    draft_id = uuid4()
    service.request_release(
        aggregate,
        release_id=draft_id,
        codebook_id=uuid4(),
        profile_name="exploratory",
        creator="alice",
        manifest=ReleaseManifest(
            release_id=str(draft_id),
            codebook_id=str(uuid4()),
            codebook_version=str(uuid4()),
            codebook_sha256="0" * 64,
            profile={},
            method_declaration={},
            machine_profile={},
            created_at="t",
        ),
        requested_at="t",
    )
    with pytest.raises(ReleaseStateError, match="released"):
        propose_from_alarms(
            aggregate,
            parent_release_id=draft_id,
            parent_manifest=dict(aggregate.releases[str(draft_id)]["manifest_json"]),
            alarms=[Alarm("drift_alarm", "drift", "dropped", {})],
            version_id=UUID(str(aggregate.id)),
            sink=sink,
            proposed_at="t2",
        )


# ---------------------------------------------------------------------------
# U48 consensus wiring: typed reader + aggregation + N-run orchestration
# ---------------------------------------------------------------------------


def test_consensus_settings_read_live_config_fail_loud() -> None:
    from corpus_kb.config import get_default_config
    from corpus_kb.research.cycle_consensus import consensus_settings

    settings = consensus_settings(dict(get_default_config()))
    assert settings.runs == 3
    assert settings.min_run_fraction == 0.5
    assert settings.similarity_threshold == 0.8
    assert consensus_settings({"research": {"consensus": {"runs": 1}}}).runs == 1
    for bad in ({"runs": 0}, {"runs": True}, {"runs": "many"}):
        with pytest.raises(ValueError, match="runs"):
            consensus_settings({"research": {"consensus": bad}})
    with pytest.raises(ValueError, match="min_run_fraction"):
        consensus_settings({"research": {"consensus": {"min_run_fraction": 1.5}}})
    with pytest.raises(ValueError, match="similarity_threshold"):
        consensus_settings({"research": {"consensus": {"similarity_threshold": 2.0}}})


def _seed(run_id: str, label: str, vector: list[float]) -> Any:
    from corpus_kb.research.cycle_consensus import ProposalSeed

    return ProposalSeed(
        run_id=run_id,
        cluster_id=f"{run_id}:{label}",
        label=label,
        centroid=vector,
        first_member_text="verbatim member unit text",
    )


def test_consensus_from_seeds_aggregates_and_reports() -> None:
    from corpus_kb.research.cycle_consensus import ConsensusSettings, consensus_from_seeds

    settings = ConsensusSettings(runs=2, min_run_fraction=0.5, similarity_threshold=0.8)
    base = [1.0, 0.0, 0.0]
    other = [0.0, 1.0, 0.0]
    seeds = [
        _seed("r1", "cost", base),
        _seed("r1", "speed", other),
        _seed("r2", "price", base),  # same direction as r1:cost -> one theme
        _seed("r2", "latency", other),
    ]
    result = consensus_from_seeds(seeds, settings, 2)
    assert result.n_runs == 2
    assert result.themes_kept == 2
    assert result.quote_not_found_rejected == 0
    assert result.run_to_run_agreement

    empty = consensus_from_seeds([], settings, 2)
    assert empty.themes_kept == 0
    assert "nothing to aggregate" in empty.note


async def test_run_inductive_consensus_merges_receipts_or_fails_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from corpus_kb.research import cycle_consensus

    seeds = [_seed("r1", "cost", [1.0, 0.0]), _seed("r2", "price", [0.99, 0.01])]

    async def fake_seeds(_pool: Any, _tenant: UUID, run_ids: list[str]) -> list[Any]:
        return seeds if len(run_ids) == 2 else []

    monkeypatch.setattr(cycle_consensus, "load_proposal_seeds", fake_seeds)
    counters = {"n": 0}

    async def run_once() -> dict[str, Any]:
        counters["n"] += 1
        return {
            "status": "success",
            "run_id": f"r{counters['n']}",
            "dbcv_drop_flag": counters["n"] == 2,
            "dbcv_relative_validity": 0.4,
            "dbcv_baseline": 0.6,
        }

    cfg = {
        "research": {"consensus": {"runs": 2, "min_run_fraction": 0.5, "similarity_threshold": 0.8}}
    }
    receipt = await cycle_consensus.run_inductive_consensus(None, TENANT, cfg, None, run_once)
    assert receipt is not None
    assert receipt["status"] == "success"
    assert receipt["runs_completed"] == 2
    assert receipt["run_ids"] == ["r1", "r2"]
    assert receipt["dbcv_drop_flag"] is True  # ANY-pass merge, never papered
    assert receipt["consensus"]["themes_kept"] == 1

    async def failing_once() -> dict[str, Any]:
        return {"status": "unavailable", "reason": "inductive extra missing"}

    verbatim = await cycle_consensus.run_inductive_consensus(
        None, TENANT, {"research": {"consensus": {"runs": 3}}}, None, failing_once
    )
    assert verbatim == {"status": "unavailable", "reason": "inductive extra missing"}

    single = await cycle_consensus.run_inductive_consensus(
        None, TENANT, {"research": {"consensus": {"runs": 1}}}, None, run_once
    )
    assert single is None  # the single-run path is unchanged
