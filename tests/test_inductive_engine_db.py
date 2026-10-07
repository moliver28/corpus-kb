"""Fixture E2E for the inductive engine (todo 15) — 3 latent themes + noise.

Requires BOTH Postgres (requires_postgres, auto-skipped by the conftest TCP
probe) AND the optional ``inductive`` extra (umap-learn + hdbscan — skipped
extras-less, the CI shape). Local evidence run:

    pytest tests/test_inductive_engine_db.py -v

Seeds a synthetic corpus of three tight 1024-d themes + random noise +
between-theme boundary units directly into the read models, injects a
deterministic fake LLM + a text-keyed fake embedder, and drives the REAL
``run_inductive`` + ``promote_proposal`` path over the real UMAP/HDBSCAN
stack: clusters recovered, noise queued (never discarded), proposals with
provenance, ISR per batch in checkpoints, DBCV tracked, identical re-run
clusters (determinism pins), and human promotion into codebook v(n+1) with
re-derived prototype refs.
"""

from __future__ import annotations

import json
import uuid as uuid_mod
from typing import Any, cast

import numpy as np
import pytest

pytest.importorskip("hdbscan")
pytest.importorskip("umap")

pytestmark = pytest.mark.requires_postgres

DIM = 1024
THEME_SIZE = 20
NOISE_SIZE = 8
N_THEMES = 3
PILOT_UNITS = 60


def _unit(v: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.9g}" for x in v) + "]"


def _build_corpus() -> list[tuple[str, list[float], bool]]:
    """Deterministic synthetic corpus: (answer text, vector, qdep)."""
    rng = np.random.default_rng(20260215)
    centers = [rng.normal(size=DIM).astype(np.float32) for _ in range(N_THEMES)]
    centers = [c / np.linalg.norm(c) for c in centers]
    rows: list[tuple[str, list[float], bool]] = []
    for t, center in enumerate(centers):
        for m in range(THEME_SIZE):
            v = center + 0.02 * rng.normal(size=DIM).astype(np.float32)
            v /= np.linalg.norm(v)
            qdep = m % 4 == 0
            text = (
                f"theme{t} point{m}: depends on context"
                if qdep
                else f"theme{t} point{m}: direct observation of the workflow"
            )
            rows.append((text, [float(x) for x in v], qdep))
    for n in range(NOISE_SIZE):
        v = rng.normal(size=DIM).astype(np.float32)
        v /= np.linalg.norm(v)
        rows.append((f"unrelated noise remark {n} about the weather", [float(x) for x in v], False))
    between = centers[0] + centers[1]
    between /= np.linalg.norm(between)
    rows.append(
        ("boundary unit sitting between theme0 and theme1", [float(x) for x in between], False)
    )
    return rows


class FakeLlm:
    """Deterministic LLM double: echoes the answer as the observation."""

    model = "fake-llm"

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        options: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        user = messages[-1]["content"]
        if "Existing codes:" in user:
            return {
                "model": self.model,
                "message": {"role": "assistant", "content": '{"decision": "new"}'},
            }
        answer = user.split("Participant answer:\n", 1)[1].split("\n\n", 1)[0]
        payload = json.dumps({"summary": answer, "question_dependent": "depends" in answer})
        return {
            "model": self.model,
            "message": {"role": "assistant", "content": payload},
        }


class FakeEmbedder:
    """Text-keyed embedder double backed by the corpus vectors."""

    model = "fake-embed"
    model_revision = "1024"

    @staticmethod
    def vector_for(text: str) -> list[float] | None:
        for answer, vector, _qdep in _build_corpus():
            if text.startswith(answer):
                return vector
        return None

    async def embed_cached(self, tenant_id: Any, text: str) -> list[float] | None:
        return self.vector_for(text)


class _StubEmbedder:
    model = "stub"
    model_revision = "1024"


async def _seed_read_models(pool: Any, tenant_id: str) -> None:
    from corpus_kb.storage.tenant_conn import tenant_connection

    corpus = _build_corpus()
    doc_id = str(uuid_mod.uuid4())
    async with tenant_connection(pool, tenant_id) as conn:
        await conn.execute(
            """
            INSERT INTO documents (doc_id, tenant_id, source, source_type)
            VALUES ($1, $2, 'fixture://inductive', 'transcript')
            ON CONFLICT (tenant_id, source) DO NOTHING
            """,
            doc_id,
            tenant_id,
        )
        for seq, (answer, vector, _qdep) in enumerate(corpus):
            sha_q = uuid_mod.uuid4().hex
            sha_a = uuid_mod.uuid4().hex
            exchange_id = await conn.fetchval(
                """
                INSERT INTO research_exchanges
                (tenant_id, doc_id, seq, q_unit_ids, a_unit_ids, question_text,
                 qa_text, stance, term_origin, embedding, embedding_model,
                 model_revision, dimensions)
                VALUES ($1, $2, $3, '{}', '{}', $4, $5, 'affirm', 'participant',
                        $6::vector, 'fake-embed', '1024', 1024)
                RETURNING exchange_id
                """,
                tenant_id,
                doc_id,
                seq,
                f"Q: {answer}",
                f"Q: {answer} || {answer}",
                _unit(np.asarray(vector)),
            )
            q_id = await conn.fetchval(
                """
                INSERT INTO research_units
                (tenant_id, doc_id, seq, text, text_sha256, role_in_exchange,
                 exchange_id, is_codable, embedding, embedding_model,
                 model_revision, dimensions)
                VALUES ($1, $2, $3, $4, $5, 'question', $6, FALSE, $7::vector,
                        'fake-embed', '1024', 1024)
                RETURNING unit_id
                """,
                tenant_id,
                doc_id,
                seq * 2,
                f"Q: {answer}",
                sha_q,
                int(exchange_id),
                _unit(np.asarray(vector)),
            )
            a_id = await conn.fetchval(
                """
                INSERT INTO research_units
                (tenant_id, doc_id, seq, text, text_sha256, role_in_exchange,
                 exchange_id, is_codable, embedding, embedding_model,
                 model_revision, dimensions)
                VALUES ($1, $2, $3, $4, $5, 'answer', $6, TRUE, $7::vector,
                        'fake-embed', '1024', 1024)
                RETURNING unit_id
                """,
                tenant_id,
                doc_id,
                seq * 2 + 1,
                answer,
                sha_a,
                int(exchange_id),
                _unit(np.asarray(vector)),
            )
            await conn.execute(
                "UPDATE research_exchanges SET q_unit_ids = $4, a_unit_ids = $5 "
                "WHERE tenant_id = $1 AND exchange_id = $2 AND doc_id = $3",
                tenant_id,
                int(exchange_id),
                doc_id,
                [int(q_id)],
                [int(a_id)],
            )


async def _proposal_members(pool: Any, tenant_id: str, run_id: str) -> list[frozenset[int]]:
    from corpus_kb.storage.tenant_conn import tenant_connection

    async with tenant_connection(pool, tenant_id) as conn:
        rows = await conn.fetch(
            """
            SELECT member_unit_ids FROM research_proposed_codes
            WHERE tenant_id = $1 AND run_id = $2
            """,
            tenant_id,
            run_id,
        )
    return sorted(frozenset(int(x) for x in row["member_unit_ids"]) for row in rows)


async def _first_proposed_id(pool: Any, tenant_id: str, run_id: str) -> int:
    from corpus_kb.storage.tenant_conn import tenant_connection

    async with tenant_connection(pool, tenant_id) as conn:
        value = await conn.fetchval(
            """
            SELECT proposed_id FROM research_proposed_codes
            WHERE tenant_id = $1 AND run_id = $2 AND status = 'proposed'
            ORDER BY n_members DESC LIMIT 1
            """,
            tenant_id,
            run_id,
        )
    assert value is not None
    return int(value)


async def _count_versions(pool: Any, tenant_id: str) -> int:
    from corpus_kb.storage.tenant_conn import tenant_connection

    async with tenant_connection(pool, tenant_id) as conn:
        value = await conn.fetchval(
            "SELECT COUNT(*) FROM codebook_versions WHERE tenant_id = $1", tenant_id
        )
    return int(value or 0)


async def _registry_theory(pool: Any, tenant_id: str, code_id: str) -> dict[str, Any]:
    from corpus_kb.storage.tenant_conn import tenant_connection

    async with tenant_connection(pool, tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT theory FROM code_registry WHERE tenant_id = $1 AND code_id = $2",
            tenant_id,
            code_id,
        )
    assert row is not None
    theory = row["theory"]
    if isinstance(theory, str):
        theory = json.loads(theory or "{}")
    return dict(theory)


@pytest.fixture()
async def seeded_db():
    from tests import research_db

    dsn = await research_db.create_research_db()
    research_db.set_env(dsn)
    await research_db.reset_singletons()
    import asyncpg

    pool = await asyncpg.create_pool(dsn)
    try:
        await _seed_read_models(pool, research_db.TEST_TENANT)
        yield pool, research_db.TEST_TENANT
    finally:
        await pool.close()
        await research_db.drop_research_db()


@pytest.mark.asyncio
async def test_fixture_e2e_clusters_noise_proposals_determinism_promotion(seeded_db):
    from corpus_kb.domain.application import get_app
    from corpus_kb.domain.codebook import CodebookVersion
    from corpus_kb.projections.checkpoint import CheckpointManager
    from corpus_kb.projections.dlq import DLQHandler
    from corpus_kb.projections.event_reader import EventReader
    from corpus_kb.projections.research._embed import ResearchEmbedder
    from corpus_kb.projections.research_projection import ResearchProjection
    from corpus_kb.research.inductive_run import run_inductive
    from corpus_kb.research.promote_code import promote_proposal

    pool, tenant_id = seeded_db
    fake = FakeEmbedder()
    summary1 = await run_inductive(pool, tenant_id, llm=FakeLlm(), embedder=fake)
    assert summary1["status"] == "success", summary1
    n_units = int(str(summary1["n_units"]))
    n_clusters = int(str(summary1["n_clusters"]))
    noise_units = int(str(summary1["noise_units"]))
    batches = int(str(summary1["batches"]))
    high_entropy = int(str(summary1["high_entropy"]))
    meta_new = int(str(summary1["meta_new"]))

    total = 3 * THEME_SIZE + NOISE_SIZE + 1
    assert n_units == total
    assert n_clusters >= 3
    assert noise_units >= 1
    assert batches == (total - PILOT_UNITS + 24) // 25
    assert high_entropy >= 1
    assert meta_new >= 1
    assert isinstance(summary1["dbcv_relative_validity"], float)
    assert summary1["dbcv_drop_flag"] is False

    run_id1 = str(summary1["run_id"])
    members1 = await _proposal_members(pool, tenant_id, run_id1)
    assert len(members1) == n_clusters
    assert all(len(m) >= 5 for m in members1)  # min_cluster_size floor honored
    assert len({u for m in members1 for u in m}) >= 3 * 5  # themes recovered

    # Determinism: identical re-run produces identical cluster membership.
    summary2 = await run_inductive(pool, tenant_id, llm=FakeLlm(), embedder=fake)
    assert summary2["status"] == "success"
    members2 = await _proposal_members(pool, tenant_id, str(summary2["run_id"]))
    assert members2 == members1

    await _assert_support_rows(pool, tenant_id, run_id1, n_units, batches)

    # Human promotion gate -> codebook v(n+1) with re-derived prototypes.
    proposed_id = await _first_proposed_id(pool, tenant_id, str(summary2["run_id"]))
    n_versions_before = await _count_versions(pool, tenant_id)
    result = await promote_proposal(
        pool,
        tenant_id,
        proposed_id,
        "Theme Observation",
        "Units describing direct observations in theme 0.",
    )
    assert result["status"] == "promoted", result
    n_members = int(str(result["n_members"]))
    assert n_members >= 5

    app = get_app()
    aggregate = app.repository.get(uuid_mod.UUID(str(result["codebook_version_id"])))
    assert isinstance(aggregate, CodebookVersion)
    assert aggregate.paradigm == "inductive"
    code = aggregate.codes[str(result["code_id"])]
    assert code["mode"] == "inductive"
    refs = list(code["exemplar_text_sha256"])
    assert len(refs) == n_members

    projection = ResearchProjection(
        pool,
        CheckpointManager(pool),
        DLQHandler(pool),
        ResearchEmbedder(pool, _StubEmbedder()),
    )
    reader = EventReader(pool, app.mapper, app.recorder.events_table_name)
    await projection.catch_up(reader)
    theory = await _registry_theory(pool, tenant_id, str(result["code_id"]))
    assert theory["mode"] == "inductive"
    assert list(theory["exemplar_text_sha256"]) == refs
    assert await _count_versions(pool, tenant_id) == n_versions_before + 1


async def _assert_support_rows(
    pool: Any, tenant_id: str, run_id: str, n_units: int, batches: int
) -> None:
    from corpus_kb.domain.application import get_app
    from corpus_kb.domain.coding import CodingRun
    from corpus_kb.storage.tenant_conn import tenant_connection

    incremental = n_units - PILOT_UNITS
    async with tenant_connection(pool, tenant_id) as conn:
        signals = await conn.fetch(
            """
            SELECT soft_cluster_entropy, n_clusters FROM research_signals
            WHERE tenant_id = $1 AND run_id = $2 AND tier = 0
            """,
            tenant_id,
            run_id,
        )
        noise = await conn.fetch(
            """
            SELECT status, probability FROM research_noise_queue
            WHERE tenant_id = $1 AND run_id = $2
            """,
            tenant_id,
            run_id,
        )
        observations = await conn.fetch(
            """
            SELECT question_dependent, prompt_id, temperature, parse_fallback
            FROM research_observations WHERE tenant_id = $1 AND run_id = $2
            """,
            tenant_id,
            run_id,
        )
    assert len(signals) == incremental  # Tier-0 rows for the growth batch only
    assert all(int(row["n_clusters"]) >= 2 for row in signals)
    assert any(float(row["soft_cluster_entropy"]) > 0.8 for row in signals)
    assert len(noise) >= 1
    assert all(row["status"] == "pending" for row in noise)
    assert len(observations) == n_units
    qdep_rows = [row for row in observations if row["question_dependent"]]
    assert len(qdep_rows) >= 1
    assert all(row["prompt_id"] == "inductive.atomic_observation.v1" for row in observations)
    assert all(float(row["temperature"]) == 0.0 for row in observations)
    assert all(not row["parse_fallback"] for row in observations)
    # The event-sourced run trail: manifest checkpoint + per-batch ISR checkpoints
    # + the final DBCV/noise accounting.
    run = get_app().repository.get(uuid_mod.UUID(run_id))
    assert isinstance(run, CodingRun)
    checkpoints: list[dict[str, object]] = list(run.checkpoints)
    kinds = [str(cp.get("kind")) for cp in checkpoints]
    assert "manifest" in kinds
    assert "batch" in kinds
    assert "final" in kinds
    batch_cps = [cp for cp in checkpoints if cp.get("kind") == "batch"]
    assert len(batch_cps) == batches
    assert all("isr" in cp for cp in batch_cps)
    assert all(0.0 <= float(str(cp["isr"])) <= 1.0 for cp in batch_cps)
    final: dict[str, object] = checkpoints[-1]
    assert final["dbcv_relative_validity"] is not None
    assert int(str(final["noise_units"])) >= 1
    assert int(str(final["batches"])) == batches
    manifest_cp = next(cp for cp in checkpoints if cp.get("kind") == "manifest")
    recorded = cast(dict[str, object], manifest_cp["manifest"])
    assert recorded["umap_random_state"] == 42
    assert recorded["umap_transform_seed"] == 42
    assert int(str(recorded["n_components"])) == 10
    assert recorded["metric"] == "cosine"
