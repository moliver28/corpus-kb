"""DuckDB flag-plumbing pins for the inductive run.

The inductive store twins take a ``duck`` flag, but the run pipeline never
arms it. The baseline read sent the Postgres-only JSON ``?`` probe to DuckDB,
so every run died with a ParserException before the nothing-to-cluster
envelope. These legs pin the armed twins and the envelope on one migrated
DuckDB file. Hermetic: no Postgres, no Ollama, no optional umap extra.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from corpus_kb._setup.migrate import run_migrations
from corpus_kb.research.inductive_run import run_inductive
from corpus_kb.research.inductive_store import latest_baseline_dbcv, load_unit_views_by_ids
from corpus_kb.research.run_inputs import load_gold_views
from corpus_kb.storage.duckdb_gateway import DuckDbGateway
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string, vector_literal

TENANT = UUID("00000000-0000-0000-0000-000000000001")
BASELINE_RUN_ID = UUID("33333333-3333-3333-3333-333333333333")
DIM = 1024


async def _migrated_gateway(tmp_path: Path, name: str) -> DuckDbGateway:
    path = tmp_path / name
    await run_migrations(duckdb_path_to_conn_string(path))
    return await DuckDbGateway.connect(str(path))


def _duck_cfg(path: Path) -> dict[str, object]:
    """Config that binds the run's event store to this file's sidecar."""
    return {
        "database": {
            "backend": "duckdb",
            "connection_string": duckdb_path_to_conn_string(path),
        }
    }


async def _seed_run_with_checkpoint(
    gateway: DuckDbGateway, run_id: UUID, started_at: datetime, checkpoint: dict[str, object] | None
) -> None:
    await gateway.execute(
        """
        INSERT INTO research_runs (run_id, tenant_id, params, state, checkpoint, started_at)
        VALUES ($1, $2, '{}', 'stopped', $3, $4)
        """,
        str(run_id),
        str(TENANT),
        json.dumps(checkpoint) if checkpoint is not None else None,
        started_at,
    )


@pytest.mark.asyncio
async def test_latest_baseline_dbcv_duck_arm_on_empty_corpus(tmp_path: Path) -> None:
    """The duck arm answers None on an empty corpus (no crash, no rows)."""
    gateway = await _migrated_gateway(tmp_path, "baseline-empty.duckdb")
    try:
        baseline = await latest_baseline_dbcv(gateway, TENANT, BASELINE_RUN_ID, duck=True)
        assert baseline is None
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_latest_baseline_dbcv_duck_arm_scans_newest_first(tmp_path: Path) -> None:
    """The duck scan skips checkpoints without the key and reads newest-first."""
    gateway = await _migrated_gateway(tmp_path, "baseline-scan.duckdb")
    try:
        now = datetime.now(tz=UTC)
        old = now - timedelta(hours=2)
        new = now - timedelta(hours=1)
        await _seed_run_with_checkpoint(
            gateway, BASELINE_RUN_ID, old, {"dbcv_relative_validity": 0.7}
        )
        # A newer run checkpointed a manifest only: no DBCV key in it.
        await _seed_run_with_checkpoint(gateway, uuid4(), new, {"kind": "manifest"})
        baseline = await latest_baseline_dbcv(gateway, TENANT, uuid4(), duck=True)
        assert baseline == 0.7
        # The seeded baseline run is excluded from its own probe.
        assert await latest_baseline_dbcv(gateway, TENANT, BASELINE_RUN_ID, duck=True) is None
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_run_inductive_empty_corpus_envelope_on_duckdb(tmp_path: Path) -> None:
    """An empty-corpus run answers the envelope, never a ParserException."""
    name = "envelope.duckdb"
    gateway = await _migrated_gateway(tmp_path, name)
    try:
        summary = await run_inductive(gateway, TENANT, cfg=_duck_cfg(tmp_path / name))
        assert summary["status"] == "success"
        assert summary["n_units"] == 0
        assert "nothing to cluster" in str(summary["note"])
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_run_inductive_seeded_units_write_fallback_observations(tmp_path: Path) -> None:
    """Seeded codable units reach the pair read and the observation write."""
    name = "seeded.duckdb"
    gateway = await _migrated_gateway(tmp_path, name)
    try:
        doc_id = str(uuid4())
        await gateway.execute(
            "INSERT INTO documents (doc_id, tenant_id, source, source_type)"
            " VALUES ($1, $2, $3, 'transcript')",
            doc_id,
            str(TENANT),
            "sym://inductive",
        )
        # The BIGINT[] columns get explicit empty literals: the table's
        # '{}' VARCHAR default cannot coerce to BIGINT[] on DuckDB.
        await gateway.execute(
            "INSERT INTO research_exchanges (tenant_id, doc_id, seq, q_unit_ids, a_unit_ids)"
            " VALUES ($1, $2, 0, [], [])",
            str(TENANT),
            doc_id,
        )
        turns = [
            ("question", "How do you trust management here?", False),
            ("answer", "We trust them; pay has stayed fair.", True),
        ]
        for seq, (role, text, codable) in enumerate(turns):
            await gateway.execute(
                """
                INSERT INTO research_units
                    (tenant_id, doc_id, seq, text, text_sha256, exchange_id,
                     role_in_exchange, is_codable)
                VALUES ($1, $2, $3, $4, $5, 1, $6, $7)
                """,
                str(TENANT),
                doc_id,
                seq,
                text,
                f"{seq:064x}",
                role,
                codable,
            )
        summary = await run_inductive(gateway, TENANT, cfg=_duck_cfg(tmp_path / name))
        # No embedder is injected, so the pass still ends at the envelope.
        assert summary["status"] == "success"
        assert summary["n_units"] == 0
        rows = await gateway.fetch(
            "SELECT prompt_id, parse_fallback FROM research_observations WHERE tenant_id = $1",
            str(TENANT),
        )
        assert len(rows) == 1
        assert str(rows[0]["prompt_id"]) == "fallback.raw_answer"
    finally:
        await gateway.close()


def _unit_vec(index: int) -> list[float]:
    vec = [0.0] * DIM
    vec[index] = 1.0
    return vec


async def _seed_promotion_world(gateway: DuckDbGateway) -> int:
    """One proposal over three member units with complete A/QA/Q views."""
    doc_id = str(uuid4())
    await gateway.execute(
        "INSERT INTO documents (doc_id, tenant_id, source, source_type)"
        " VALUES ($1, $2, $3, 'transcript')",
        doc_id,
        str(TENANT),
        "sym://promote",
    )
    await gateway.execute(
        "INSERT INTO research_exchanges (tenant_id, doc_id, seq, q_unit_ids, a_unit_ids,"
        " embedding) VALUES ($1, $2, 0, [], [], $3::FLOAT[1024])",
        str(TENANT),
        doc_id,
        vector_literal(_unit_vec(0)),
    )
    unit_ids: list[int] = []
    turns = [("question", False), ("answer", True), ("answer", True), ("answer", True)]
    for seq, (role, codable) in enumerate(turns):
        unit_id = await gateway.fetchval(
            """
            INSERT INTO research_units
                (tenant_id, doc_id, seq, text, text_sha256, exchange_id,
                 role_in_exchange, is_codable, embedding)
            VALUES ($1, $2, $3, $4, $5, 1, $6, $7, $8::FLOAT[1024])
            RETURNING unit_id
            """,
            str(TENANT),
            doc_id,
            seq,
            f"turn text {seq}",
            f"{seq:064x}",
            role,
            codable,
            vector_literal(_unit_vec(seq)),
        )
        unit_ids.append(int(unit_id))
    proposed_id = await gateway.fetchval(
        """
        INSERT INTO research_proposed_codes
            (tenant_id, cluster_id, run_id, suggested_label, member_unit_ids, n_members)
        VALUES ($1, 'c1', $2, 'trust', $3, 3)
        RETURNING proposed_id
        """,
        str(TENANT),
        str(BASELINE_RUN_ID),
        unit_ids[1:],
    )
    return int(proposed_id)


@pytest.mark.asyncio
async def test_load_unit_views_and_gold_views_on_duck(tmp_path: Path) -> None:
    """The promotion reads answer on duck: the armed twin and the shimmed read."""
    gateway = await _migrated_gateway(tmp_path, "views.duckdb")
    try:
        proposed_id = await _seed_promotion_world(gateway)
        row = await gateway.fetchrow(
            "SELECT member_unit_ids FROM research_proposed_codes WHERE proposed_id = $1",
            proposed_id,
        )
        member_ids = [int(x) for x in row["member_unit_ids"]]
        views = await load_unit_views_by_ids(gateway, TENANT, member_ids, duck=True)
        assert len(views) == 3
        sha = f"{0:064x}"
        gold = await load_gold_views(gateway, TENANT, [sha])
        assert len(gold) == 1
        assert gold[0][0] is not None
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_promote_proposal_on_duckdb_promotes_seeded_proposal(tmp_path: Path) -> None:
    """The human promotion gate mints a version and flips the proposal row."""
    name = "promote.duckdb"
    gateway = await _migrated_gateway(tmp_path, name)
    try:
        proposed_id = await _seed_promotion_world(gateway)
        from corpus_kb.research.promote_code import promote_proposal

        result = await promote_proposal(
            gateway,
            TENANT,
            proposed_id,
            "trust",
            "trust in management",
            dsn=duckdb_path_to_conn_string(tmp_path / name),
        )
        assert result["status"] == "promoted"
        assert result["n_members"] == 3
        status = await gateway.fetchval(
            "SELECT status FROM research_proposed_codes WHERE proposed_id = $1",
            proposed_id,
        )
        assert status == "promoted"
    finally:
        await gateway.close()
