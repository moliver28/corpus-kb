"""DB-backed retrieval tests (todo 12): citation contract, tenancy proof,
instruct() routing, scoping filters, exchange-embedding foundation.

Runs on the throwaway research DB (requires_postgres) with the deterministic
1024-dim FakeEmbedder; retrieval quality vs baseline is proven live in
test_retrieval_benchmark.py (requires_ollama).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from research_db import TEST_TENANT

from corpus_kb.handlers.research_handler import get_research_handler
from corpus_kb.projections.checkpoint import CheckpointManager
from corpus_kb.projections.dlq import DLQHandler
from corpus_kb.projections.event_reader import EventReader
from corpus_kb.projections.research._embed import ResearchEmbedder
from corpus_kb.projections.research_projection import ResearchProjection
from corpus_kb.rag.embedder import QUERY_INSTRUCTION_PREFIX, FakeEmbedder
from corpus_kb.research.retrieval import Citation, ResearchQuery, research_search

pytestmark = pytest.mark.requires_postgres

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "research"
TENANT = UUID(TEST_TENANT)
MEETING_TXT = "\n".join(
    [
        "Alice: We need to plan the launch timeline for the billing feature.",
        "Bob: Engineering needs two sprints for the billing migration work.",
        "Cathy: Design will deliver the mockups by Friday for review.",
        "Dave: Marketing wants a teaser campaign before the announcement.",
        "Eve: Let us reconvene after the mockup review next week.",
    ]
)


class SpyEmbedder(FakeEmbedder):
    """Records every text it is asked to embed (instruct() assertion)."""

    def __init__(self) -> None:
        super().__init__({"embedding": {"dimensions": 1024}})
        self.texts: list[str] = []

    def embed_batch(self, texts):  # type: ignore[override]
        self.texts.extend(texts)
        return super().embed_batch(texts)


async def _ingest_fixture(pool, project_id: UUID, workdir: Path) -> str:
    from corpus_kb.domain.application import get_app
    from corpus_kb.handlers.research_handler import reset_research_handler

    reset_research_handler()
    app = get_app()
    reader = EventReader(pool, app.mapper, app.recorder.events_table_name)
    embedder = ResearchEmbedder(pool, FakeEmbedder({"embedding": {"dimensions": 1024}}))
    projection = ResearchProjection(pool, CheckpointManager(pool), DLQHandler(pool), embedder)
    handler = get_research_handler(pool, embed_fn=lambda text: [0.1] * 1024)

    target = workdir / "retrieval_fixture.md"
    shutil.copyfile(FIXTURES / "retrieval_fixture.md", target)
    result = await handler.ingest_transcript(TENANT, str(target), project_id=project_id)
    assert result["status"] == "success"
    assert result["n_exchanges"] == 26
    await projection.catch_up(reader)
    return str(result["doc_id"])


async def _search(pool, project_id: UUID, query: str, **kwargs: object) -> list[Citation]:
    embedder = ResearchEmbedder(pool, FakeEmbedder({"embedding": {"dimensions": 1024}}))
    return await research_search(
        pool,
        embedder,
        ResearchQuery(query=query, tenant_id=TENANT, project_id=project_id, **kwargs),  # type: ignore[arg-type]
    )


async def test_parent_exchange_citation_contract(research_pool, tmp_path: Path) -> None:
    project = uuid4()
    await _ingest_fixture(research_pool, project, tmp_path)
    hits = await _search(research_pool, project, "password reset email locked out of login", k=5)
    assert hits, "no citations returned for a lexical-exact query"
    top = hits[0]
    assert isinstance(top, Citation)
    assert top.exchange_seq is not None
    assert top.question_text and "password reset flow" in top.question_text.lower()
    assert "locked me out of login" in top.answer_text
    # citation contract: question above the answer, answer highlighted
    start, end = top.answer_highlight
    assert 0 <= start < end <= len(top.answer_text)
    assert top.answer_text[start:end]
    assert top.matched_kind in {"answer", "qa", "question"}
    assert top.doc_title == "retrieval_fixture"


async def test_wrong_project_id_returns_zero_rows(research_pool, tmp_path: Path) -> None:
    project = uuid4()
    await _ingest_fixture(research_pool, project, tmp_path)
    outsider = uuid4()
    hits = await _search(research_pool, outsider, "password reset email", k=10)
    assert hits == [], "tenancy proof failed: another project saw exchange rows"


async def test_query_embedding_routes_through_instruct(research_pool, tmp_path: Path) -> None:
    project = uuid4()
    await _ingest_fixture(research_pool, project, tmp_path)
    spy = SpyEmbedder()
    embedder = ResearchEmbedder(research_pool, spy)
    await research_search(
        research_pool,
        embedder,
        ResearchQuery(query="ticket queue", tenant_id=TENANT, project_id=project, k=3),
    )
    instructed = [t for t in spy.texts if QUERY_INSTRUCTION_PREFIX in t]
    assert instructed, "query-side embed never received the instruct() prefix"
    assert any("ticket queue" in t for t in instructed)


async def test_exchange_embeddings_and_unit_backfill(research_pool, tmp_path: Path) -> None:
    project = uuid4()
    await _ingest_fixture(research_pool, project, tmp_path)
    async with research_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", str(TENANT))
        embedded_exchanges = await conn.fetchval(
            """
            SELECT count(*) FROM research_exchanges e
            JOIN documents d ON d.doc_id = e.doc_id
            WHERE d.project_id = $1 AND e.embedding IS NOT NULL
            """,
            project,
        )
        linked_units, total_units = await conn.fetchrow(
            """
            SELECT count(*) FILTER (WHERE u.exchange_id IS NOT NULL),
                   count(*) AS total
            FROM research_units u
            JOIN documents d ON d.doc_id = u.doc_id
            WHERE d.project_id = $1
            """,
            project,
        )
    assert embedded_exchanges == 26, "exchange (qa child) embeddings missing"
    assert int(linked_units) == int(total_units) == 52, "units.exchange_id backfill incomplete"


async def test_scoping_filters_narrow_results(research_pool, tmp_path: Path) -> None:
    project = uuid4()
    await _ingest_fixture(research_pool, project, tmp_path)
    baseline = await _search(research_pool, project, "migration scripts data import", k=25)
    assert baseline
    by_source = await _search(
        research_pool, project, "migration scripts data import", source_type="transcript", k=25
    )
    assert by_source, "source_type=transcript filter must keep transcript rows"
    wrong_source = await _search(
        research_pool, project, "migration scripts data import", source_type="code", k=25
    )
    assert wrong_source == [], "source_type=code must exclude transcript exchanges"
    by_doc = await _search(
        research_pool,
        project,
        "migration scripts data import",
        doc_ids=(baseline[0].doc_id,),
        k=25,
    )
    assert by_doc and all(c.doc_id == baseline[0].doc_id for c in by_doc)
    participant_only = await _search(
        research_pool, project, "migration scripts data import", speaker_role="participant", k=25
    )
    assert participant_only, "participant answers must survive the role filter"
    moderator_only = await _search(
        research_pool, project, "migration scripts data import", speaker_role="moderator", k=25
    )
    assert moderator_only, "moderator question children must survive the role filter"
    assert all(hit.matched_kind == "question" for hit in moderator_only), (
        "moderator filter must match only moderator-authored (question) children"
    )


async def test_meeting_windows_serve_as_parents(research_pool, tmp_path: Path) -> None:
    from corpus_kb.domain.application import get_app
    from corpus_kb.handlers.research_handler import reset_research_handler

    reset_research_handler()
    app = get_app()
    reader = EventReader(research_pool, app.mapper, app.recorder.events_table_name)
    embedder = ResearchEmbedder(research_pool, FakeEmbedder({"embedding": {"dimensions": 1024}}))
    projection = ResearchProjection(
        research_pool, CheckpointManager(research_pool), DLQHandler(research_pool), embedder
    )
    handler = get_research_handler(research_pool, embed_fn=lambda text: [0.1] * 1024)
    meeting = tmp_path / "team_sync.txt"
    meeting.write_text(MEETING_TXT, encoding="utf-8")
    result = await handler.ingest_transcript(TENANT, str(meeting), project_id=uuid4())
    assert result["status"] == "success"
    await projection.catch_up(reader)

    hits = await _search(
        research_pool,
        UUID(int=0),
        "billing migration timeline",
        k=5,
    )
    assert hits == [], "wrong project must return nothing even for windows"

    async with research_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", str(TENANT))
        project_id = await conn.fetchval(
            "SELECT project_id FROM documents WHERE source LIKE '%team_sync%'"
        )
    assert project_id is not None
    window_hits = await _search(
        research_pool, UUID(str(project_id)), "billing migration timeline", k=5
    )
    assert window_hits, "window parent not returned for facilitator-less source"
    top = window_hits[0]
    assert top.exchange_seq is None, "meeting hits must not claim an exchange parent"
    assert top.question_text is None
    assert "billing migration" in top.answer_text
    start, end = top.answer_highlight
    assert "billing migration" in top.answer_text[start:end].lower()
