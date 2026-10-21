"""Research retrieval happy paths on DuckDB (sp-research-port).

Hermetic: migrated DuckDB file, seeded read models, the deterministic
FakeEmbedder for the query leg. The proofs cover the ported twins:

- the exact-scan dense arm + bm25 lexical arm fuse through the pure
  rrf_fuse port and land parent citations in the migration-024 order;
- the order is deterministic across repeated runs (zero-drift rule 9);
- tenant + project scoping holds, and the doc-id filter flows through the
  ``list_contains`` membership twin.
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
from corpus_kb.research.retrieval import ResearchQuery, research_search
from corpus_kb.storage.duckdb_gateway import DuckDbGateway
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string, vector_literal

TENANT = "00000000-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-000000000002"
PROJECT = "11111111-1111-1111-1111-111111111111"
PROJECT_OTHER = "12222222-2222-2222-2222-222222222222"
DIM = 1024
QUERY = "grid investment"


def _normalized(vec: list[float]) -> list[float]:
    """L2-normalize to the D6 unit contract the embedder enforces."""
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def _probe(text: str) -> list[float]:
    """The exact query-side vector research_search will embed for ``text``."""
    fake = FakeEmbedder({"embedding": {"dimensions": DIM}})
    return _normalized(fake.embed(instruct(text)))


async def _migrated_gateway(tmp_path: Path) -> DuckDbGateway:
    path = tmp_path / "retrieval.duckdb"
    await run_migrations(duckdb_path_to_conn_string(path))
    return await DuckDbGateway.connect(str(path))


async def _seed(gateway: DuckDbGateway) -> dict[str, str]:
    """Two same-project exchanges (one on-probe, one off-probe) + a foreign one."""
    doc_a, doc_b = str(uuid4()), str(uuid4())
    for doc_id, source in ((doc_a, "sym://a"), (doc_b, "sym://b")):
        await gateway.execute(
            "INSERT INTO documents (doc_id, tenant_id, source, source_type, project_id, title)"
            " VALUES ($1, $2, $3, 'transcript', $4, $5)",
            doc_id,
            TENANT,
            source,
            PROJECT,
            source,
        )
    await gateway.execute(
        "INSERT INTO documents (doc_id, tenant_id, source, source_type, project_id, title)"
        " VALUES ($1, $2, $3, 'transcript', $4, $5)",
        str(uuid4()),
        OTHER,
        "sym://foreign",
        PROJECT_OTHER,
        "sym://foreign",
    )
    await gateway.execute(
        "INSERT INTO research_projects (project_id, tenant_id, name) VALUES ($1, $2, $3)",
        PROJECT,
        TENANT,
        "climate",
    )

    async def _unit(doc_id: str, seq: int, text: str, vec: list[float]) -> int:
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
            vector_literal(vec),
        )
        row = await gateway.fetchval(
            "SELECT unit_id FROM research_units WHERE tenant_id = $1 AND doc_id = $2 AND seq = $3",
            TENANT,
            doc_id,
            seq,
        )
        return int(row)

    on_probe = _probe(QUERY)
    off_probe = _normalized(_probe("unrelated topic matter")[::-1])
    a1 = await _unit(doc_a, 1, "The grid investment plan accelerates.", on_probe)
    a2 = await _unit(doc_b, 2, "The grid investment plan stalls.", off_probe)
    for doc_id, seq, a_id, vec in ((doc_a, 1, a1, on_probe), (doc_b, 2, a2, off_probe)):
        await gateway.execute(
            "INSERT INTO research_exchanges (tenant_id, doc_id, seq, q_unit_ids, a_unit_ids,"
            " question_text, qa_text, embedding)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, $8::FLOAT[1024])",
            TENANT,
            doc_id,
            seq,
            [],
            [a_id],
            "What about the grid investment plan?",
            "Q: What about the grid investment plan? A: see the answer",
            vector_literal(vec),
        )
    return {"doc_a": doc_a, "doc_b": doc_b}


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


@pytest.mark.asyncio
async def test_retrieval_fuses_both_arms_deterministically(tmp_path: Path) -> None:
    gateway = await _migrated_gateway(tmp_path)
    try:
        await _seed(gateway)
        first = await research_search(gateway, _embedder(gateway), _query())
        second = await research_search(gateway, _embedder(gateway), _query())
        assert first, "expected at least one citation"
        assert [(c.doc_id, c.answer_text, c.score) for c in first] == [
            (c.doc_id, c.answer_text, c.score) for c in second
        ]
        scores = [c.score for c in first]
        assert scores == sorted(scores, reverse=True)
        top = first[0]
        assert top.answer_text == "The grid investment plan accelerates."
        assert top.question_text == "What about the grid investment plan?"
        assert top.matched_kind in {"answer", "qa"}
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_retrieval_scopes_by_project_and_doc_ids(tmp_path: Path) -> None:
    gateway = await _migrated_gateway(tmp_path)
    try:
        seeded = await _seed(gateway)
        other_project = await research_search(
            gateway,
            _embedder(gateway),
            _query(tenant_id=UUID(OTHER), project_id=UUID(PROJECT_OTHER)),
        )
        assert other_project == []

        doc_a = UUID(seeded["doc_a"])
        scoped = await research_search(
            gateway,
            _embedder(gateway),
            _query(doc_ids=(doc_a,)),
        )
        assert scoped
        for citation in scoped:
            assert citation.doc_id == doc_a
    finally:
        await gateway.close()
