"""Hermetic release-lifecycle E2E on DuckDB (storage portability, sp-release-port).

One tmp ``.duckdb`` file through the :class:`DuckDbGateway` carries the
gate-evidence reads (code_registry, audit_partitions, research_runs); the
CodebookVersion aggregate — the lifecycle truth — rides the
``eventsourcing.sqlite`` store on a SEPARATE file (SQLite cannot open a
file DuckDB holds). The handlers run unchanged: the only port is the
pool/gateway coercion at the tenant-connection seam. The config env vars
point the aggregate seam at the event-store file (``database.backend:
duckdb`` truth). No Postgres, no Ollama.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from corpus_kb._setup.migrate import run_duckdb_migrations
from corpus_kb.domain.codebook import CodebookVersion
from corpus_kb.handlers import release_handlers as rh
from corpus_kb.storage.duckdb_gateway import DuckDbGateway
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string

TENANT = UUID("00000000-0000-0000-0000-000000000001")
CODES: list[dict[str, object]] = [
    {"code_id": "c1", "name": "trust", "definition": "Expressions of trust."},
    {"code_id": "c2", "name": "risk", "definition": "Statements about risk."},
]
OFF_CFG: dict[str, object] = {"research": {"release": {"enforcement": "off"}}}
ENFORCE_CFG: dict[str, object] = {
    "research": {"release": {"enforcement": "enforce", "default_profile": "team-codebook"}}
}


@pytest.fixture
def event_store_path(tmp_path: Path) -> str:
    return str(tmp_path / "events.store")


@pytest.fixture
async def gateway(tmp_path: Path):
    db = tmp_path / "release.duckdb"
    run_duckdb_migrations(duckdb_path_to_conn_string(str(db)))
    gw = await DuckDbGateway.connect(duckdb_path_to_conn_string(str(db)))
    yield gw
    await gw.close()


@pytest.fixture(autouse=True)
def _duckdb_config_env(tmp_path: Path, event_store_path: str, monkeypatch: pytest.MonkeyPatch):
    """Point the config-truth seams at the hermetic event store.

    A minimal config file (CORPUS_KB_CONFIG is discovery slot 2) carries
    the backend switch + DSN without the Postgres-only engine selections
    the repo default config sets (the loader's mix gate refuses those).
    """
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "database:\n"
        "  backend: duckdb\n"
        f"  connection_string: {event_store_path.replace(chr(92), '/')}\n"
        "graph:\n"
        "  backend: postgres\n"
        "extraction:\n"
        "  backend: regex\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CORPUS_KB_CONFIG", str(cfg))
    # CI exports CORPUS_KB_DATABASE_URL (postgres) on every leg; the env
    # override beats the config file, so drop it for a hermetic duckdb run.
    monkeypatch.delenv("CORPUS_KB_DATABASE_URL", raising=False)


@pytest.fixture(autouse=True)
def _close_event_apps():
    from corpus_kb.domain.application import reset_app

    yield
    reset_app()


async def _save_aggregate(event_store_path: str) -> CodebookVersion:
    from corpus_kb.domain.application import get_app

    app = get_app(event_store_path, backend="duckdb")
    aggregate = CodebookVersion(tenant_id=TENANT, label="v1", sha256="c" * 64)
    aggregate.add_codes(TENANT, CODES)
    app.save(aggregate)
    return aggregate


async def _seed_evidence(gw: DuckDbGateway, version_id: UUID) -> None:
    for code in CODES:
        await gw.execute(
            "INSERT INTO code_registry (code_id, tenant_id, codebook_version_id, name, "
            "brief_definition, inclusion_criteria, exclusion_criteria) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            str(code["code_id"]),
            str(TENANT),
            str(version_id),
            str(code["name"]),
            str(code["definition"]),
            "units that include it",
            "units that exclude it",
        )
    rows = [
        (1, "discovery", "s1", 0.5),
        (2, "audit", "s1", 1.0),
    ]
    for unit_id, partition, stratum, probability in rows:
        await gw.execute(
            "INSERT INTO audit_partitions (tenant_id, scope, unit_id, partition, "
            "stratum, inclusion_probability, seed) VALUES (?, ?, ?, ?, ?, ?, ?)",
            str(TENANT),
            "projects",
            unit_id,
            partition,
            stratum,
            probability,
            42,
        )


async def test_release_lifecycle_legs_on_duckdb(
    gateway: DuckDbGateway, event_store_path: str
) -> None:
    from corpus_kb.domain.application import get_app

    aggregate = await _save_aggregate(event_store_path)
    await _seed_evidence(gateway, aggregate.id)

    # 1 ASSESS: gate evidence composes over the gateway; writes nothing.
    assessed = await rh.assess_release(gateway, TENANT, aggregate.id)
    payload = assessed.to_payload()
    assert payload["status"] == "assessed"
    sufficiency = payload["data_sufficiency"]
    assert sufficiency["codebook"] is True
    assert sufficiency["partitions"] is True
    assert sufficiency["audit_sample"] is True
    assert payload["gate_results"], "every registered gate reports a status"
    assert all(
        gate["status"] in ("pass", "fail", "not_evaluable") for gate in payload["gate_results"]
    )

    # Determinism (program rule 9): the same evidence, the same gate table.
    again = await rh.assess_release(gateway, TENANT, aggregate.id)
    assert again.to_payload() == payload

    # 2 REQUEST: the draft lands in the sqlite event store.
    receipt = await rh.request_release(gateway, TENANT, aggregate.id, "alice")
    assert receipt.status == "requested"
    assert receipt.manifest_sha256
    release_id = UUID(receipt.release_id)

    # 3 REVIEW: the packet is honest before any gate ran.
    packet = await rh.review_release(gateway, TENANT, aggregate.id, release_id)
    review = packet.to_payload()
    assert review["release_state"] == "draft_candidate"
    assert review["evidence"] == []

    # 4 RELEASE (enforcement off): gates recorded + the transition applied.
    outcome = await rh.execute_release(
        gateway, TENANT, aggregate.id, release_id, "dana", config=OFF_CFG
    )
    assert outcome.status == "released"
    released_state = (
        get_app(event_store_path, backend="duckdb")
        .repository.get(aggregate.id)
        .releases[str(release_id)]["state"]
    )
    assert released_state == "released"

    # 5 STATUS: the stage derives from the aggregate, evidence via the gateway.
    report = await rh.status_report(gateway, TENANT, aggregate.id)
    status = report.to_payload()
    assert status["current_stage"] == "released"
    assert status["active_release"]["release_id"] == str(release_id)
    assert status["data_sufficiency"]["codebook"] is True

    # 6 COMPARE: manifests rebuilt from the event store diff cleanly. The
    # manifest hash covers created_at, so two real releases never hash the
    # same; the CONTENT deltas must all be empty for the same codes.
    same = await rh.compare_version_releases(TENANT, aggregate.id, release_id, release_id)
    assert same.to_payload()["identical"] is True
    receipt_b = await rh.request_release(gateway, TENANT, aggregate.id, "alice")
    release_b = UUID(receipt_b.release_id)
    outcome_b = await rh.execute_release(
        gateway, TENANT, aggregate.id, release_b, "dana", config=OFF_CFG
    )
    assert outcome_b.status == "released"
    compared = await rh.compare_version_releases(TENANT, aggregate.id, release_id, release_b)
    diff_payload = compared.to_payload()
    assert diff_payload["identical"] is False
    for content_key in (
        "codes_added",
        "codes_removed",
        "codes_merged",
        "definitions_changed",
        "method_changes",
        "threshold_changes",
        "metric_deltas",
    ):
        assert not diff_payload[content_key], content_key


async def test_release_blocked_under_enforce_keeps_the_draft_on_duckdb(
    gateway: DuckDbGateway, event_store_path: str
) -> None:
    from corpus_kb.domain.application import get_app

    aggregate = await _save_aggregate(event_store_path)
    await _seed_evidence(gateway, aggregate.id)
    receipt = await rh.request_release(gateway, TENANT, aggregate.id, "alice")
    release_id = UUID(receipt.release_id)

    outcome = await rh.execute_release(
        gateway, TENANT, aggregate.id, release_id, "dana", config=ENFORCE_CFG
    )
    assert outcome.status == "blocked"
    assert outcome.unmet_required_gates, "not_evaluable required gates block under enforce"
    state = (
        get_app(event_store_path, backend="duckdb")
        .repository.get(aggregate.id)
        .releases[str(release_id)]["state"]
    )
    assert state == "draft_candidate"


async def test_assess_run_id_reliability_leg_reads_runs_over_the_gateway(
    gateway: DuckDbGateway, event_store_path: str
) -> None:
    import json

    aggregate = await _save_aggregate(event_store_path)
    await _seed_evidence(gateway, aggregate.id)
    await gateway.execute(
        "INSERT INTO research_runs (run_id, tenant_id, checkpoint) VALUES (?, ?, ?)",
        "11111111-1111-1111-1111-111111111111",
        str(TENANT),
        json.dumps({"reliability": {"irr": 0.82}, "conformal": {"per_unit": {"1": 3}}}),
    )
    run_id = UUID("11111111-1111-1111-1111-111111111111")
    assessed = await rh.assess_release(gateway, TENANT, aggregate.id, run_id=run_id)
    sufficiency = assessed.to_payload()["data_sufficiency"]
    assert sufficiency["reliability"] is True
