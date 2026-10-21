"""Research store legs on DuckDB (sp-research-port).

Hermetic: one migrated DuckDB file, the gateway as the session, no LLM and
no Postgres. Covers the four store surfaces the program lists for this WP:

- analytics SQL: the exact-scan kNN twins (``analytics_sql_duckdb``) return
  the same residual ranking contract, and ``explain_plan`` reads the DuckDB
  EXPLAIN row shape;
- batching: the run markers mark idempotently and resume skips completed
  items (the merge-patch twin);
- inductive store: observation/signal/proposal/noise writes round-trip with
  the fixed-size vector casts and the JSON columns read back parsed;
- exact cache: the JSON write twin and the parsed read.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from corpus_kb._setup.migrate import run_migrations
from corpus_kb.research.analytics_sql import explain_plan, residual_scan, uncoded_units_ranking
from corpus_kb.research.batching import RunMarker, mark_item_completed, run_resumable
from corpus_kb.research.exact_cache import CacheKeyComponents
from corpus_kb.research.exact_cache import get as cache_get
from corpus_kb.research.exact_cache import put as cache_put
from corpus_kb.research.inductive_store import (
    load_codable_pairs,
    queue_noise,
    record_signal,
    upsert_observation,
    upsert_proposal,
)
from corpus_kb.storage.duckdb_gateway import DuckDbGateway
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string, vector_literal

TENANT = UUID("00000000-0000-0000-0000-000000000001")
RUN_ID = UUID("33333333-3333-3333-3333-333333333333")
DIM = 1024


def _vec(index: int) -> list[float]:
    vec = [0.0] * DIM
    vec[index] = 1.0
    return vec


def _slice_256(vec: list[float]) -> list[float]:
    sliced = vec[:256]
    norm = math.sqrt(sum(x * x for x in sliced)) or 1.0
    return [x / norm for x in sliced]


async def _migrated_gateway(tmp_path: Path, name: str) -> DuckDbGateway:
    path = tmp_path / name
    await run_migrations(duckdb_path_to_conn_string(path))
    return await DuckDbGateway.connect(str(path))


async def _seed_units(gateway: DuckDbGateway, count: int) -> None:
    doc_id = str(uuid4())
    await gateway.execute(
        "INSERT INTO documents (doc_id, tenant_id, source, source_type)"
        " VALUES ($1, $2, $3, 'transcript')",
        doc_id,
        TENANT,
        "sym://analytics",
    )
    for seq in range(count):
        await gateway.execute(
            "INSERT INTO research_units (tenant_id, doc_id, seq, text, text_sha256,"
            " role_in_exchange, is_codable, embedding, embedding_256)"
            " VALUES ($1, $2, $3, $4, $5, 'answer', TRUE, $6::FLOAT[1024], $7::FLOAT[256])",
            TENANT,
            doc_id,
            seq,
            f"answer text {seq}",
            f"{seq:064x}",
            vector_literal(_vec(seq)),
            vector_literal(_slice_256(_vec(seq))),
        )


@pytest.mark.asyncio
async def test_analytics_exact_scan_twins_rank_and_explain(tmp_path: Path) -> None:
    gateway = await _migrated_gateway(tmp_path, "analytics.duckdb")
    try:
        await _seed_units(gateway, 4)
        proto = _vec(2)
        on_proto = int(
            await gateway.fetchval(
                "SELECT unit_id FROM research_units WHERE tenant_id = $1 AND seq = 2", TENANT
            )
        )
        residuals = await residual_scan(gateway, [proto], k=4, duck=True)
        assert residuals
        ranked = await uncoded_units_ranking(gateway, [proto], k=4, duck=True)
        # The radar sorts ASCENDING max-cosine: the on-proto unit lands last,
        # the orthogonal ones (similarity 0, tied) fill the front — and the
        # order repeats across runs (zero-drift rule 9).
        again = await uncoded_units_ranking(gateway, [proto], k=4, duck=True)
        assert [r["unit_id"] for r in ranked] == [r["unit_id"] for r in again]
        assert [r["r"] for r in ranked] == sorted(r["r"] for r in ranked)
        assert int(ranked[-1]["unit_id"]) == on_proto
        assert [r["r"] for r in ranked] == sorted(r["r"] for r in ranked)
        plan = await explain_plan(
            gateway,
            "SELECT unit_id FROM research_units WHERE tenant_id = $1",
            [TENANT],
            duck=True,
        )
        assert "research_units" in plan
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_batching_marks_idempotently_and_resumes(tmp_path: Path) -> None:
    gateway = await _migrated_gateway(tmp_path, "batching.duckdb")
    try:
        await gateway.execute(
            "INSERT INTO research_runs (run_id, tenant_id, llm_name, embed_model, state)"
            " VALUES ($1, $2, 'test', 'test', 'started')",
            RUN_ID,
            TENANT,
        )
        marker = RunMarker(tenant_id=str(TENANT), run_id=str(RUN_ID))
        assert await mark_item_completed(gateway, marker, "item-1", duck=True) is True
        assert await mark_item_completed(gateway, marker, "item-1", duck=True) is False

        async def _work(item: str) -> str:
            return f"done:{item}"

        results = await run_resumable(
            gateway, marker, ["item-1", "item-2"], lambda item: item, _work, duck=True
        )
        assert results == ["done:item-2"]  # item-1 already marked, skipped
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_inductive_store_round_trips_on_duckdb(tmp_path: Path) -> None:
    gateway = await _migrated_gateway(tmp_path, "inductive.duckdb")
    try:
        doc_id = str(uuid4())
        await gateway.execute(
            "INSERT INTO documents (doc_id, tenant_id, source, source_type)"
            " VALUES ($1, $2, $3, 'transcript')",
            doc_id,
            TENANT,
            "sym://inductive",
        )
        for seq, (question, answer) in enumerate(
            [("why?", "because the grid holds"), ("when?", "next quarter")], start=1
        ):
            role = "main_question" if question == "why?" else "answer"
            await gateway.execute(
                "INSERT INTO research_units (tenant_id, doc_id, seq, text, text_sha256,"
                " role_in_exchange, embedding) VALUES ($1, $2, $3, $4, $5, $6, $7::FLOAT[1024])",
                TENANT,
                doc_id,
                seq,
                question if role == "main_question" else answer,
                f"{seq:064x}",
                role,
                vector_literal(_vec(seq)),
            )
        unit_id = int(
            await gateway.fetchval(
                "SELECT unit_id FROM research_units WHERE tenant_id = $1 AND seq = 2", TENANT
            )
        )
        question_id = int(
            await gateway.fetchval(
                "SELECT unit_id FROM research_units WHERE tenant_id = $1 AND seq = 1", TENANT
            )
        )
        await gateway.execute(
            "INSERT INTO research_exchanges (tenant_id, doc_id, seq, q_unit_ids, a_unit_ids,"
            " question_text, qa_text) VALUES ($1, $2, 1, $3, $4, $5, $6)",
            TENANT,
            doc_id,
            [question_id],
            [unit_id],
            "why?",
            "Q: why? A: because the grid holds",
        )
        await gateway.execute(
            "UPDATE research_units SET exchange_id = 1 WHERE tenant_id = $1 AND doc_id = $2",
            TENANT,
            doc_id,
        )
        await upsert_observation(
            gateway,
            TENANT,
            unit_id,
            RUN_ID,
            summary="the grid holds",
            question_dependent=True,
            prompt_id="p1",
            model="test",
            temperature=0.0,
            parse_fallback=False,
            embedding=_vec(3),
            embed_model="test",
            model_revision="1024",
            duck=True,
        )
        await record_signal(
            gateway,
            TENANT,
            unit_id,
            RUN_ID,
            soft_cluster_entropy=0.5,
            n_clusters=2,
            extra={"note": "tier0"},
            duck=True,
        )
        await upsert_proposal(
            gateway,
            TENANT,
            cluster_id=f"{RUN_ID}:cluster:0",
            run_id=RUN_ID,
            batch_no=1,
            suggested_label="grid",
            centroid=_vec(3),
            member_unit_ids=[unit_id],
            dbcv_relative_validity=0.42,
            duck=True,
        )
        await queue_noise(gateway, TENANT, unit_id, RUN_ID, probability=0.9, entropy=1.5)

        pairs = await load_codable_pairs(gateway, TENANT, None, duck=True)
        assert pairs and pairs[0]["answer"] == "next quarter"
        proposal = await gateway.fetchrow(
            "SELECT centroid, member_unit_ids, dbcv_relative_validity"
            " FROM research_proposed_codes WHERE tenant_id = ?",
            TENANT,
        )
        assert proposal is not None
        assert len(proposal["centroid"]) == DIM
        assert list(proposal["member_unit_ids"]) == [unit_id]
        signal = await gateway.fetchrow(
            "SELECT extra FROM research_signals WHERE tenant_id = ? AND unit_id = ?",
            TENANT,
            unit_id,
        )
        assert signal is not None
        extra = signal["extra"]
        assert (extra if isinstance(extra, dict) else json.loads(str(extra)))["note"] == "tier0"
        noise = await gateway.fetchval(
            "SELECT count(*) FROM research_noise_queue WHERE tenant_id = ?", TENANT
        )
        assert int(noise) == 1
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_exact_cache_put_get_round_trip_on_duckdb(tmp_path: Path) -> None:
    gateway = await _migrated_gateway(tmp_path, "cache.duckdb")
    try:
        components = CacheKeyComponents(
            unit_text_sha256="a" * 64,
            codebook_release_sha256="b" * 64,
            prompt_template_sha256="c" * 64,
            model_digest="d" * 64,
            decoding_params_json="{}",
            schema_sha256="e" * 64,
        )
        payload = {"answer": "grid holds", "confidence": 0.8}
        await cache_put(
            gateway, str(TENANT), "key-1", components, payload, "test-model", "d" * 64, duck=True
        )
        hit = await cache_get(gateway, str(TENANT), "key-1")
        assert hit == payload  # DuckDB's text-form JSON reads back parsed
        miss = await cache_get(gateway, str(TENANT), "key-miss")
        assert miss is None
    finally:
        await gateway.close()
