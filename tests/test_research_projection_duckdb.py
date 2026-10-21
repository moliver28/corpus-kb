"""Research projection catch-up symmetry on DuckDB (sp-research-port).

Hermetic, mirroring the phase-D projections-port legs: the event store is
the eventsourcing SQLite recorder file, the read models live in a migrated
DuckDB file behind the SqlGateway, and the embedder is the deterministic
FakeEmbedder at the research D6 dimension (1024). The proofs:

- incremental catch-up materializes exactly the new events' rows (speakers,
  units, exchanges, links) with embeddings written;
- replay-from-scratch converges to the same materialized state (idempotent);
- a foreign-tenant event advances the shared checkpoint without rows.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from uuid import UUID

import pytest

from corpus_kb._setup.migrate import run_migrations
from corpus_kb.domain.aggregates import Document
from corpus_kb.domain.application import CorpusApplication
from corpus_kb.projections.checkpoint import CheckpointManager
from corpus_kb.projections.dlq import DLQHandler
from corpus_kb.projections.event_reader import EventReader
from corpus_kb.projections.research._common import (
    DEFAULT_TENANT,
    RESEARCH_PROJECTION_NAME,
    store_turn_texts,
)
from corpus_kb.projections.research._embed import ResearchEmbedder
from corpus_kb.projections.research_projection import ResearchProjection
from corpus_kb.rag.embedder import FakeEmbedder
from corpus_kb.storage.duckdb_gateway import DuckDbGateway
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string

TENANT = UUID("00000000-0000-0000-0000-000000000001")
OTHER = UUID("00000000-0000-0000-0000-000000000002")
PROJECT_NAME = "climate-hearings"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _migrated_gateway(tmp_path: Path, name: str) -> DuckDbGateway:
    path = tmp_path / name
    await run_migrations(duckdb_path_to_conn_string(path))
    return await DuckDbGateway.connect(str(path))


def _app(tmp_path: Path) -> CorpusApplication:
    return CorpusApplication(connection_string=str(tmp_path / "events.db"), backend="duckdb")


def _reader(app: CorpusApplication) -> EventReader:
    return EventReader(None, app.mapper, app.recorder.events_table_name, recorder=app.recorder)


def _projection(
    gateway: DuckDbGateway, checkpoint: CheckpointManager, dlq: DLQHandler
) -> ResearchProjection:
    embedder = ResearchEmbedder(
        None, FakeEmbedder({"embedding": {"dimensions": 1024}}), gateway=gateway
    )
    return ResearchProjection(None, checkpoint, dlq, embedder, gateway=gateway)


def _turn(text: str, seq: int, exchange_role: str, speaker_role: str) -> dict[str, object]:
    """One turn payload shaped like the handler flow: the speaker role is
    the mapped vocabulary (CHECK-enforced), role_in_exchange the syntactic
    kind the citation layer reads."""
    return {
        "seq": seq,
        "text_sha256": _sha(text),
        "speaker": speaker_role,
        "role": speaker_role,
        "role_in_exchange": exchange_role,
    }


def _new_document(tenant: UUID, source: str, metadata: dict[str, object] | None = None) -> Document:
    return Document(
        tenant_id=tenant,
        source=source,
        source_type="transcript",
        file_hash=uuid.uuid4().hex,
        metadata=metadata or {},
    )


async def _snapshot(gateway: DuckDbGateway) -> dict[str, list[tuple[object, ...]]]:
    """Deterministic read-model state (order pinned by each table's key)."""
    state: dict[str, list[tuple[object, ...]]] = {}
    state["research_speakers"] = [
        tuple(r.values())
        for r in await gateway.fetch(
            "SELECT tenant_id, doc_id, raw_label, role FROM research_speakers ORDER BY 1, 2, 3"
        )
    ]
    state["research_units"] = [
        tuple(r.values())
        for r in await gateway.fetch(
            "SELECT tenant_id, doc_id, seq, text_sha256, role_in_exchange, exchange_id"
            " FROM research_units ORDER BY doc_id, seq"
        )
    ]
    state["research_exchanges"] = [
        tuple(r.values())
        for r in await gateway.fetch(
            "SELECT tenant_id, doc_id, seq, question_text, qa_text, a_unit_ids"
            " FROM research_exchanges ORDER BY doc_id, seq"
        )
    ]
    state["research_projects"] = [
        tuple(r.values())
        for r in await gateway.fetch(
            "SELECT project_id, tenant_id, name FROM research_projects ORDER BY 1"
        )
    ]
    return state


async def _catch_up(gateway: DuckDbGateway, app: CorpusApplication) -> int:
    checkpoint = CheckpointManager(gateway=gateway)
    dlq = DLQHandler(gateway=gateway)
    projection = _projection(gateway, checkpoint, dlq)
    return await projection.catch_up(_reader(app))


@pytest.mark.asyncio
async def test_catchup_is_symmetric_and_idempotent(tmp_path: Path) -> None:
    gateway = await _migrated_gateway(tmp_path, "research-symmetry.duckdb")
    app = _app(tmp_path)
    try:
        # Ingest-style event flow: Ingested (with project metadata), then a
        # turn batch whose texts ride the immutable content-addressed store,
        # then an exchange link batch.
        doc = _new_document(
            TENANT,
            "sym://hearing-one",
            {"project_id": str(uuid.uuid4()), "project_name": PROJECT_NAME},
        )
        app.save(doc)
        doc_id = UUID(str(doc.id))

        q_text = "What did the panel conclude about the 2030 target?"
        a_text = "The panel concluded the target is achievable with grid investment."
        await store_turn_texts(gateway, TENANT, {_sha(a_text): a_text, _sha(q_text): q_text})
        saved = app.repository.get(doc_id)
        saved.add_turn_batch(
            TENANT,
            [
                _turn(q_text, 1, "main_question", "moderator"),
                _turn(a_text, 2, "answer", "participant"),
            ],
        )
        saved.add_exchange_batch(
            TENANT,
            [{"seq": 1, "q_unit_seqs": [1], "a_unit_seqs": [2], "link_method": "adjacency"}],
        )
        app.save(saved)

        processed = await _catch_up(gateway, app)
        assert processed >= 3  # Ingested + TurnsParsed + ExchangesLinked

        rows = await gateway.fetch(
            "SELECT unit_id, role_in_exchange, text FROM research_units ORDER BY seq"
        )
        assert [r["role_in_exchange"] for r in rows] == ["main_question", "answer"]
        assert rows[1]["text"] == a_text
        exchange = await gateway.fetchrow(
            "SELECT qa_text, a_unit_ids FROM research_exchanges WHERE tenant_id = ?", TENANT
        )
        assert exchange is not None
        assert "panel concluded" in str(exchange["qa_text"])
        assert list(exchange["a_unit_ids"]) == [int(rows[1]["unit_id"])]
        linked = await gateway.fetchval(
            "SELECT exchange_id FROM research_units WHERE tenant_id = ? AND seq = 2", TENANT
        )
        assert linked is not None
        project = await gateway.fetchrow(
            "SELECT name FROM research_projects WHERE tenant_id = ?", TENANT
        )
        assert project is not None and project["name"] == PROJECT_NAME
        vectors = await gateway.fetchval(
            "SELECT count(*) FROM research_exchanges WHERE tenant_id = ? AND embedding IS NOT NULL",
            TENANT,
        )
        assert int(vectors) == 1  # the exchange qa embed landed

        # Replay from a reset checkpoint converges to the same state.
        await gateway.execute(
            "DELETE FROM projection_checkpoints WHERE projection_name = ?", RESEARCH_PROJECTION_NAME
        )
        before = await _snapshot(gateway)
        await _catch_up(gateway, app)
        assert await _snapshot(gateway) == before
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_foreign_tenant_event_advances_checkpoint_without_rows(tmp_path: Path) -> None:
    gateway = await _migrated_gateway(tmp_path, "research-tenant.duckdb")
    app = _app(tmp_path)
    try:
        app.save(_new_document(OTHER, "sym://foreign"))
        checkpoint = CheckpointManager(gateway=gateway)
        dlq = DLQHandler(gateway=gateway)
        projection = _projection(gateway, checkpoint, dlq)
        processed = await projection.catch_up(_reader(app))
        assert processed == 1
        for table in ("research_speakers", "research_units", "research_exchanges"):
            count = await gateway.fetchval(f"SELECT count(*) FROM {table}")
            assert int(count) == 0, table
        last = await checkpoint.get_checkpoint(RESEARCH_PROJECTION_NAME, DEFAULT_TENANT)
        assert last is not None
    finally:
        await gateway.close()
