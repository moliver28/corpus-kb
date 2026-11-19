"""DuckDB arms for the offline retrieval eval (storage portability).

``evaluate_arms`` reuses the retrieval arms and the house RRF, but it never
arms their ``duck`` flags: on a DuckDB study the vector arm sends the
Postgres ``::halfvec`` probe to the engine (``Type with name halfvec does
not exist``), the fused arms reach for ``corpus.rrf_fusion``, and
``load_reviewed_labels`` asks an asyncpg pool it does not have. The legs
here pin the armed eval on one migrated DuckDB file. Hermetic: no Postgres,
no Ollama.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import UUID

import pytest

from corpus_kb._setup.migrate import run_migrations
from corpus_kb.research.retrieval_eval import ARMS, EvalQuery, evaluate_arms, load_reviewed_labels
from corpus_kb.research.retrieval_settings import load_retrieval_settings
from corpus_kb.storage.duckdb_gateway import DuckDbGateway
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string
from tests._research_duckdb_seeds import (
    PROJECT,
    QUESTION,
    TENANT,
    VERSION,
    probe,
    seed_ask_fixture,
    seed_radar_fixture,
)


class _ProbeEmbedder:
    """The eval embedder seam over the seed fixture's FakeEmbedder probe."""

    async def embed_cached(self, tenant_id: UUID, text: str) -> list[float] | None:
        return probe(text)


async def _seeded_gateway(
    tmp_path: Path, name: str, seeder: Callable[[str], Awaitable[None]]
) -> DuckDbGateway:
    """Migrate, seed (the seeder owns its own gateway), then hold one."""
    path = tmp_path / name
    dsn = duckdb_path_to_conn_string(str(path))
    await run_migrations(dsn)
    await seeder(dsn)
    return await DuckDbGateway.connect(str(path))


@pytest.mark.asyncio
async def test_evaluate_arms_on_duckdb_scores_all_arms(tmp_path: Path) -> None:
    """All four arms score on DuckDB through the armed twins."""
    gateway = await _seeded_gateway(tmp_path, "eval.duckdb", seed_ask_fixture)
    try:
        settings = load_retrieval_settings({"retrieval": {}})
        labeled = EvalQuery(query_id="q1", text=QUESTION, relevant=frozenset({1}))
        report = await evaluate_arms(
            gateway,
            _ProbeEmbedder(),
            [labeled],
            str(TENANT),
            str(PROJECT),
            settings,
        )
        assert report.status == "ok"
        assert set(report.arms) == set(ARMS)
        assert all(metrics.n_queries == 1 for metrics in report.arms.values())
        # The seeded answer unit embeds to the query probe itself, so the
        # armed exact-scan vector arm ranks it first.
        assert report.arms["vector"].recall_at_k > 0
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_load_reviewed_labels_on_duckdb(tmp_path: Path) -> None:
    """Reviewed assignments derive labeled queries over the gateway."""
    gateway = await _seeded_gateway(tmp_path, "labels.duckdb", seed_radar_fixture)
    try:
        await gateway.execute(
            "INSERT INTO research_assignments (tenant_id, assignment_aggregate_id, unit_id,"
            " code_id, cb_version_id, evidence_basis, confidence, sim_answer, rationale, status)"
            " VALUES ($1, 'asg-eval', 1, 'c-trust', $2, 'explicit_in_answer', 'high', 0.9,"
            " 'seeded', 'confirmed')",
            str(TENANT),
            VERSION,
        )
        labels = await load_reviewed_labels(gateway, str(TENANT), str(PROJECT))
        assert len(labels) == 1
        assert labels[0].text == "trust. Expressions of trust."
        assert labels[0].relevant == frozenset({1})
    finally:
        await gateway.close()
