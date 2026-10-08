"""F-7 convergence E2E (review F3 fix round): the gray-zone gate fires on
UNRESOLVED review-queue items and the documented remedy converges - the
cycle halts at gray_zone_escalation, the human accepts every pending item
through the REAL review surface, and the resumed cycle COMPLETES (exit 0)
with the report artifact.

requires_postgres only (CI's DB-less matrix auto-skips via the conftest TCP
probe). Two seams are test doubles, both faithful to reality:
  * the embedder is FakeEmbedder-1024 (deterministic; research requires
    exactly 1024 dims and the cycle's config-level preflight passes);
  * the inductive stage is stubbed to an honest all-noise receipt - the
    optional umap/hdbscan extra is deliberately absent in the extras-less
    local bar and on CI, this fixture corpus organically clusters to
    all-noise anyway, and the LIVE inductive path is covered by
    test_cycle_fixture on hosts with the extra + Ollama.
Everything else is the REAL pipeline: real ingest events + projections, a
real seeded codebook aggregate, a real deductive run recording review
assignments, the real review accept surface, real checkpoints and resume.
"""

from __future__ import annotations

from contextlib import ExitStack
from io import StringIO
from pathlib import Path
from typing import cast
from unittest.mock import patch
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import asyncpg
import pytest

from corpus_kb.config import load_config
from corpus_kb.handlers.research_handler import ResearchHandler
from corpus_kb.projections.checkpoint import CheckpointManager
from corpus_kb.projections.dlq import DLQHandler
from corpus_kb.projections.documents_projection import DocumentsProjection
from corpus_kb.projections.embed_projection import EmbedChunksProjection
from corpus_kb.projections.event_reader import EventReader
from corpus_kb.projections.research._embed import ResearchEmbedder
from corpus_kb.projections.research_projection import ResearchProjection
from corpus_kb.rag.embedder import FakeEmbedder
from corpus_kb.research import guide_copy
from corpus_kb.research.cycle import run_cycle
from corpus_kb.research.cycle_gates import GATE_EXIT_CODES
from corpus_kb.research.cycle_reads import pending_reviews
from corpus_kb.research.cycle_state import last_cycle_checkpoint

pytestmark = pytest.mark.requires_postgres

# The cycle-corpus fixtures (3 single-theme interviews with question/answer
# exchanges) are the same corpus test_cycle_fixture runs the deductive stage
# on; the exchange fixture would hit a latent scoring edge (an exchange whose
# moderator turn links as 'transition' leaves UnitViews.question empty and
# crashes stream_scores) that is out of this fix round's scope.
CYCLE_CORPUS = Path(__file__).resolve().parent / "fixtures" / "research" / "cycle"
TRANSCRIPTS = (
    "interview_transit.txt",
    "interview_store_closures.txt",
    "interview_benefits.txt",
)
TENANT = UUID("00000000-0000-0000-0000-00000000f3c7")
PROJECT = uuid5(NAMESPACE_URL, "f3fix/gray-zone-convergence")


def _fake_embedder(_cfg: object, _pool: object) -> FakeEmbedder:
    return FakeEmbedder({"embedding": {"dimensions": 1024}})


async def _all_noise_stage(
    pool: object, stack: object, tenant_id: object, cfg: object, project_id: object
) -> dict[str, object]:
    """An honest all-noise inductive receipt (see module docstring)."""
    return {
        "status": "success",
        "run_id": str(uuid4()),
        "n_units": 0,
        "n_clusters": 0,
        "note": "test stub: the live inductive pass is covered by test_cycle_fixture",
    }


def _patches() -> ExitStack:
    stack = ExitStack()
    stack.enter_context(patch("corpus_kb.rag.create_embedder", _fake_embedder))
    stack.enter_context(patch("corpus_kb.research.cycle.stage_inductive", _all_noise_stage))
    return stack


def _cfg(dsn: str) -> dict[str, object]:
    cfg = load_config()
    db = cast(dict[str, object], cfg["database"])
    db["connection_string"] = dsn
    # Config-level preflight must pass; the real embed calls hit the patched
    # FakeEmbedder, never Ollama.
    cfg["embedding"] = {
        "provider": "ollama",
        "model": "qwen3-embedding:8b-q8_0",
        "base_url": "http://localhost:11434",
        "dimensions": 4096,
        "batch_size": 8,
    }
    return cfg


async def _run_cycle(dsn: str, **kwargs: object) -> tuple[int, str]:
    kwargs.setdefault("cfg", _cfg(dsn))
    pool = await asyncpg.create_pool(dsn)
    captured = StringIO()
    try:
        with _patches(), patch("sys.stdout", captured):
            code = await run_cycle(pool, TENANT, **cast("dict", kwargs))  # type: ignore[arg-type]
    finally:
        await pool.close()
    return code, captured.getvalue()


def _projection_stack(
    pool: asyncpg.Pool,
) -> tuple[EventReader, ResearchProjection, DocumentsProjection, EmbedChunksProjection]:
    from corpus_kb.domain.application import get_app

    app = get_app()
    reader = EventReader(pool, app.mapper, app.recorder.events_table_name)
    checkpoint = CheckpointManager(pool)
    dlq = DLQHandler(pool)
    embedder = ResearchEmbedder(pool, FakeEmbedder({"embedding": {"dimensions": 1024}}))
    projection = ResearchProjection(pool, checkpoint, dlq, embedder)
    docs = DocumentsProjection(pool, checkpoint, dlq)
    embeds = EmbedChunksProjection(
        pool, FakeEmbedder({"embedding": {"dimensions": 1024}}), checkpoint, dlq
    )
    return reader, projection, docs, embeds


async def _last_checkpoint(dsn: str) -> dict[str, object]:
    pool = await asyncpg.create_pool(dsn)
    try:
        return await last_cycle_checkpoint(pool, TENANT)
    finally:
        await pool.close()


async def test_gray_zone_gate_converges_after_reviews(research_dsn: str, tmp_path: Path) -> None:
    pool = await asyncpg.create_pool(research_dsn)
    try:
        # -- World: real ingest events, projected with the deterministic embedder.
        reader, projection, _docs, _embeds = _projection_stack(pool)
        handler = ResearchHandler(pool)
        for name in TRANSCRIPTS:
            target = tmp_path / name
            target.write_text((CYCLE_CORPUS / name).read_text(encoding="utf-8"), encoding="utf-8")
            result = await handler.ingest_transcript(TENANT, str(target), PROJECT)
            assert result["status"] == "success"
        await projection.catch_up(reader)

        from corpus_kb.storage.tenant_conn import tenant_connection

        async with tenant_connection(pool, TENANT) as conn:
            rows = await conn.fetch(
                "SELECT text FROM research_units WHERE tenant_id = $1 AND is_codable "
                "AND role_in_exchange = 'answer' ORDER BY unit_id LIMIT 2",
                TENANT,
            )
        assert rows, "fixture ingest produced no codable units"
        exemplars = [str(r["text"]) for r in rows]

        # -- Codebook: the demo's REAL aggregate seeding path (Created +
        # CodeAdded + PrototypeUpdated), then projected into the read models
        # run_deductive reads.
        from corpus_kb.research.demo import _resolve_exemplar_shas, _seed_codebook

        codebook = {
            "label": "f3-codebook",
            "codes": [
                {
                    "name": "transportation_barrier",
                    "definition": "Units describing transit problems blocking access.",
                    "exemplars": exemplars,
                }
            ],
        }
        codes = [
            {
                "code_id": str(uuid5(NAMESPACE_URL, "f3fix/code/transportation_barrier")),
                "name": "transportation_barrier",
                "definition": "Units describing transit problems blocking access.",
                "inclusion": "",
                "exclusion": "",
            }
        ]
        shas_by_code = await _resolve_exemplar_shas(pool, TENANT, codebook)
        assert shas_by_code["transportation_barrier"], "exemplar texts must match ingested units"
        version_id = await _seed_codebook(pool, TENANT, codes, codebook, shas_by_code)
        await projection.catch_up(reader)
        assert version_id is not None
    finally:
        await pool.close()

    # -- Phase A: the cycle halts at the gray-zone gate on UNRESOLVED items.
    code, out = await _run_cycle(
        research_dsn, mode="out", project_id=PROJECT, halt_on=["gray_zone_escalation"]
    )
    assert code == GATE_EXIT_CODES["gray_zone_escalation"], out
    assert "--- deductive ---" in out
    assert guide_copy.CYCLE_HALT_HEADER.format(gate="gray_zone_escalation") in out
    checkpoint = await _last_checkpoint(research_dsn)
    assert checkpoint["kind"] == "gate_halt"
    assert checkpoint["gate"] == "gray_zone_escalation"
    assert checkpoint["stage"] == "deductive"

    pool = await asyncpg.create_pool(research_dsn)
    try:
        pending = await pending_reviews(pool, TENANT)
        assert pending > 0, "the deductive run must leave unresolved review items"
    finally:
        await pool.close()

    # -- Phase B: the human works the queue through the REAL review surface.
    pool = await asyncpg.create_pool(research_dsn)
    try:
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", str(TENANT))
            rows = await conn.fetch(
                "SELECT assignment_aggregate_id FROM research_assignments "
                "WHERE tenant_id = $1 AND status = 'review'",
                TENANT,
            )
        assert rows
        from corpus_kb.research.review_surface import execute_review

        for row in rows:
            receipt = await execute_review(
                pool,
                TENANT,
                research_dsn,
                UUID(str(row["assignment_aggregate_id"])),
                "f3-fixer",
                "accept",
            )
            assert receipt["status"] == "success"
        assert await pending_reviews(pool, TENANT) == 0, "all escalations must be resolved"
    finally:
        await pool.close()

    # -- Phase C: resume - the gate does NOT re-fire and the cycle completes.
    code, out = await _run_cycle(
        research_dsn, mode="out", project_id=PROJECT, halt_on=["gray_zone_escalation"]
    )
    assert code == 0, out
    assert "Resuming after stage: deductive" in out
    for stage in ("keywords", "report"):
        assert f"--- {stage} ---" in out, stage
    checkpoint = await _last_checkpoint(research_dsn)
    assert checkpoint["kind"] == "cycle_complete"
    assert guide_copy.CYCLE_OUTRO in out
