"""``retrieval.expand_to_parent`` on research_search (issue 201).

Hermetic DuckDB harness (the test_research_retrieval_duckdb.py
precedent): one exchange over TWO answer units, all on the probe vector.
The proofs:

- the default run is byte-identical to the explicit true run AND to the
  pre-change parent composition (ranked_children + windows +
  _to_parents + _citations): one collapsed parent citation;
- flag false returns one row per fused child (no collapsing, no
  stitching), stable across runs and in fused order (rule 9);
- the row shape never changes: the same Citation contract either way.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from corpus_kb._setup.migrate import run_migrations
from corpus_kb.projections.research._embed import ResearchEmbedder
from corpus_kb.rag.embedder import FakeEmbedder, instruct
from corpus_kb.research.citations import _citations, _meeting_windows, _to_parents
from corpus_kb.research.conn import dialect_of, tenant_scope
from corpus_kb.research.retrieval import (
    Citation,
    ResearchQuery,
    _children_from_fused,
    ranked_children,
    research_search,
)
from corpus_kb.research.retrieval_settings import RetrievalSettings, load_retrieval_settings
from corpus_kb.storage.duckdb_gateway import DuckDbGateway
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string, vector_literal

TENANT = "00000000-0000-0000-0000-000000000001"
PROJECT = "11111111-1111-1111-1111-111111111111"
DIM = 1024
QUERY = "grid investment"
QUESTION = "What about the grid investment plan?"
UNIT_A = "The grid investment plan accelerates."
UNIT_B = "The grid investment plan stalls."
QA_TEXT = f"Q: {QUESTION} A: {UNIT_A} {UNIT_B}"

PARENT_SETTINGS = load_retrieval_settings({})
CHILD_SETTINGS = RetrievalSettings(expand_to_parent=False)


def _normalized(vec: list[float]) -> list[float]:
    """L2-normalize to the D6 unit contract the embedder enforces."""
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def _probe(text: str) -> list[float]:
    """The exact query-side vector research_search will embed for ``text``."""
    fake = FakeEmbedder({"embedding": {"dimensions": DIM}})
    return _normalized(fake.embed(instruct(text)))


async def _migrated_gateway(tmp_path: Path) -> DuckDbGateway:
    path = tmp_path / "expand_parent.duckdb"
    await run_migrations(duckdb_path_to_conn_string(path))
    return await DuckDbGateway.connect(str(path))


async def _seed(gateway: DuckDbGateway) -> str:
    """One project, one document, one exchange over TWO answer units.

    Both units carry the on-probe vector and both link to the exchange,
    so the parent path collapses them into one citation. The child path
    must keep both rows plus the qa child of the exchange grain.
    """
    doc_id = str(uuid4())
    await gateway.execute(
        "INSERT INTO documents (doc_id, tenant_id, source, source_type, project_id, title)"
        " VALUES ($1, $2, $3, 'transcript', $4, $5)",
        doc_id,
        TENANT,
        "sym://a",
        PROJECT,
        "sym://a",
    )
    await gateway.execute(
        "INSERT INTO research_projects (project_id, tenant_id, name) VALUES ($1, $2, $3)",
        PROJECT,
        TENANT,
        "climate",
    )
    on_probe = _probe(QUERY)
    unit_ids: list[int] = []
    for seq, text in ((1, UNIT_A), (2, UNIT_B)):
        await gateway.execute(
            "INSERT INTO research_speakers (tenant_id, doc_id, raw_label, role)"
            " VALUES ($1, $2, $3, 'participant')",
            TENANT,
            doc_id,
            f"P{seq}",
        )
        await gateway.execute(
            "INSERT INTO research_units (tenant_id, doc_id, speaker_id, seq, text, text_sha256,"
            " role_in_exchange, embedding)"
            " VALUES ($1, $2, (SELECT speaker_id FROM research_speakers"
            "  WHERE tenant_id = $1 AND doc_id = $2 AND raw_label = $3), $4, $5, $6, 'answer',"
            " $7::FLOAT[1024])",
            TENANT,
            doc_id,
            f"P{seq}",
            seq,
            text,
            hashlib.sha256(text.encode()).hexdigest(),
            vector_literal(on_probe),
        )
        row = await gateway.fetchval(
            "SELECT unit_id FROM research_units WHERE tenant_id = $1 AND doc_id = $2 AND seq = $3",
            TENANT,
            doc_id,
            seq,
        )
        unit_ids.append(int(row))
    await gateway.execute(
        "INSERT INTO research_exchanges (tenant_id, doc_id, seq, q_unit_ids, a_unit_ids,"
        " question_text, qa_text, embedding)"
        " VALUES ($1, $2, 1, $3, $4, $5, $6, $7::FLOAT[1024])",
        TENANT,
        doc_id,
        [],
        unit_ids,
        QUESTION,
        QA_TEXT,
        vector_literal(on_probe),
    )
    # The projection backfill twin: link the units to their exchange.
    await gateway.execute(
        "UPDATE research_units SET exchange_id = (SELECT exchange_id FROM research_exchanges"
        " WHERE tenant_id = $1 AND doc_id = $2 AND seq = 1)"
        " WHERE tenant_id = $1 AND doc_id = $2 AND seq IN (1, 2)",
        TENANT,
        doc_id,
    )
    return doc_id


def _embedder(gateway: DuckDbGateway) -> ResearchEmbedder:
    return ResearchEmbedder(None, FakeEmbedder({"embedding": {"dimensions": DIM}}), gateway=gateway)


def _query(**overrides: object) -> ResearchQuery:
    fields: dict[str, object] = {
        "query": QUERY,
        "tenant_id": UUID(TENANT),
        "project_id": UUID(PROJECT),
        "k": 5,
        "rerank": False,
    }
    fields.update(overrides)
    return ResearchQuery(**fields)  # type-checked by the frozen dataclass


async def _parent_reference(
    gateway: DuckDbGateway, embedder: ResearchEmbedder, q: ResearchQuery
) -> list[Citation]:
    """The pre-change research_search body, kept here as the byte twin."""
    fused = await ranked_children(gateway, embedder, q, retrieval_settings=PARENT_SETTINGS)
    children = _children_from_fused(fused)
    duck = dialect_of(gateway).is_duckdb
    async with tenant_scope(gateway, q.tenant_id) as conn:
        windows = await _meeting_windows(conn, children, duck)
        parents = _to_parents(children, windows)
        return (await _citations(conn, parents, windows, duck))[: q.k]


@pytest.mark.asyncio
async def test_default_and_true_match_pre_change_parent_path(tmp_path: Path) -> None:
    """Default (unset) == explicit true == the pre-change composition."""
    gateway = await _migrated_gateway(tmp_path)
    try:
        await _seed(gateway)
        embedder = _embedder(gateway)
        q = _query()
        default_rows = await research_search(gateway, embedder, q)
        pinned_rows = await research_search(
            gateway, embedder, q, retrieval_settings=PARENT_SETTINGS
        )
        reference = await _parent_reference(gateway, embedder, q)
        assert default_rows == pinned_rows == reference
        assert len(default_rows) == 1, "two same-exchange children must collapse to one parent"
        top = default_rows[0]
        assert top.question_text == QUESTION
        assert top.answer_text == f"{UNIT_A} {UNIT_B}"
        assert top.matched_kind in {"answer", "qa"}
        assert top.exchange_seq == 1
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_flag_false_returns_child_rows_deterministically(tmp_path: Path) -> None:
    """Flag off: one row per fused child, stable order, same Citation shape."""
    gateway = await _migrated_gateway(tmp_path)
    try:
        await _seed(gateway)
        embedder = _embedder(gateway)
        q = _query()
        rows = await research_search(gateway, embedder, q, retrieval_settings=CHILD_SETTINGS)
        again = await research_search(gateway, embedder, q, retrieval_settings=CHILD_SETTINGS)
        assert rows and rows == again, "child rows must be stable across runs (rule 9)"
        assert sorted(c.matched_kind for c in rows) == ["answer", "answer", "qa"]
        answers = sorted(c.answer_text for c in rows if c.matched_kind == "answer")
        assert answers == sorted([UNIT_A, UNIT_B])
        qa = next(c for c in rows if c.matched_kind == "qa")
        assert qa.answer_text == QA_TEXT
        assert qa.unit_seqs == ()
        answer_seqs = sorted(c.unit_seqs for c in rows if c.matched_kind == "answer")
        assert answer_seqs == [(1,), (2,)]
        for row in rows:
            assert isinstance(row, Citation)
            assert row.question_text == QUESTION
            assert row.exchange_seq == 1
            assert row.answer_highlight == (0, len(row.answer_text))
            assert 0 <= row.answer_highlight[0] < row.answer_highlight[1]
            assert row.doc_title == "sym://a"
            assert row.project_name == "climate"
        scores = [row.score for row in rows]
        assert scores == sorted(scores, reverse=True)
    finally:
        await gateway.close()
