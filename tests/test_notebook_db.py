"""DB-backed notebook-surface tests (todo 18, requires_postgres).

Seeds a deterministic multi-code corpus (direct read-model inserts, the
test_research_report_fixture pattern), then drives the notebook ask / views
end-to-end: cited retrieval-only answers with ZERO LLM calls, the
code/confidence/review-status filters, the in-process CLI demo, and the
evidence / uncoded / overlap views.
"""

from __future__ import annotations

import json
from typing import Any, cast
from uuid import UUID, uuid4

import numpy as np
import pytest

from corpus_kb.projections.research._embed import ResearchEmbedder
from corpus_kb.rag.embedder import FakeEmbedder
from corpus_kb.research.notebook import NotebookQuery, notebook_ask
from corpus_kb.research.notebook_views import (
    evidence_for_code,
    overlap_view,
    uncoded_units,
)

pytestmark = pytest.mark.requires_postgres


def _embedder(pool: Any) -> ResearchEmbedder:
    return ResearchEmbedder(pool, FakeEmbedder({"embedding": {"dimensions": 1024}}))


DIM = 1024
CLI_TENANT = "00000000-0000-0000-0000-000000000001"
# Dedicated tenant: the session DB is shared with test_research_ingest_fixture,
# which asserts EXACT unit counts for TEST_TENANT (r-sorted after this file).
NOTEBOOK_TENANT = "00000000-0000-0000-0000-000000000003"

A_TEXT = "billing migration finished on friday after the invoice cutover"
B_TEXT = "scheduling desk reassigned technicians when the calendars collided"
H_TEXT = "weather station remark about rainfall unrelated to any work theme"

_VECTORS: dict[str, list[float]] = {}


def _base(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.normal(size=DIM).astype(np.float32)
    v /= np.linalg.norm(v)
    return v


def _blend(a: np.ndarray, b: np.ndarray, wa: float) -> np.ndarray:
    m = wa * a + (1.0 - wa) * b
    return m / np.linalg.norm(m)


def _sql_vec(v: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.9g}" for x in v) + "]"


def _theme_vec(theme: str) -> np.ndarray:
    global _VECTORS
    if not _VECTORS:
        base_a = _base(1)
        base_b = _base(2)
        base_h = _base(3)
        _VECTORS = {
            "A": base_a,
            "A2": _blend(base_a, _base(11), 0.98),
            "B": _blend(base_a, base_b, 0.99),
            "B2": _blend(base_a, base_b, 0.985),
            "H": base_h,
            "H2": _blend(base_h, _base(12), 0.98),
        }
    return _VECTORS[theme]


def _unit_text(theme: str, idx: int, text: str) -> str:
    return f"{theme} unit{idx}: {text}"


async def _seed(pool: Any, tenant_id: str, project_id: str) -> dict[str, Any]:
    """Deterministic corpus: 4 A-codable, 4 B-codable, 2 uncoded units."""
    from corpus_kb.storage.tenant_conn import tenant_connection

    out: dict[str, Any] = {"units": {}, "shas": {}, "speakers": {}}
    async with tenant_connection(pool, tenant_id) as conn:
        doc_id = str(uuid4())
        await conn.execute(
            """
            INSERT INTO documents (doc_id, tenant_id, source, source_type, project_id, title)
            VALUES ($1, $2, $3, 'interview', $4, 'notebook-fixture')
            ON CONFLICT (tenant_id, source) DO NOTHING
            """,
            doc_id,
            tenant_id,
            f"fixture://notebook/{project_id}",
            project_id,
        )
        for name, role, pseudo in (("p1", "participant", "R1"), ("mod", "moderator", "MOD")):
            out["speakers"][name] = str(
                await conn.fetchval(
                    """
                    INSERT INTO research_speakers (tenant_id, doc_id, raw_label, role, pseudonym)
                    VALUES ($1, $2, $3, $4, $5)
                    RETURNING speaker_id
                    """,
                    tenant_id,
                    doc_id,
                    name,
                    role,
                    pseudo,
                )
            )
        rows = [
            ("A", 1, A_TEXT, "p1", "answer"),
            ("A2", 2, A_TEXT, "p1", "answer"),
            ("B", 3, B_TEXT, "p1", "answer"),
            ("B2", 4, B_TEXT, "mod", "answer"),
            ("H", 5, H_TEXT, "p1", "answer"),
            ("H2", 6, H_TEXT, "p1", "answer"),
        ]
        for theme, idx, text, speaker, _role in rows:
            q_seq = idx * 10
            a_seq = idx * 10 + 1
            vec = _theme_vec(theme)
            question = f"Tell me about theme {theme}?"
            exchange_id = await conn.fetchval(
                """
                INSERT INTO research_exchanges
                (tenant_id, doc_id, seq, q_unit_ids, a_unit_ids, question_text,
                 qa_text, stance, term_origin, topic_id, embedding, embedding_256,
                 embedding_model, model_revision, dimensions)
                VALUES ($1, $2, $3, '{}', '{}', $4, $5, 'affirm', 'participant', $6,
                        $7::vector, l2_normalize(subvector($7::vector, 1, 256)),
                        'fake-embed', '1024', 1024)
                RETURNING exchange_id
                """,
                tenant_id,
                doc_id,
                idx,
                question,
                f"{question} || {text}",
                idx,
                _sql_vec(vec),
            )
            sha_q = uuid4().hex
            sha_a = uuid4().hex
            q_id = await conn.fetchval(
                """
                INSERT INTO research_units
                (tenant_id, doc_id, project_id, speaker_id, seq, t_start, text, text_sha256,
                 role_in_exchange, exchange_id, is_codable, embedding, embedding_256,
                 embedding_model, model_revision, dimensions)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'question', $9, FALSE,
                        $10::vector, l2_normalize(subvector($10::vector, 1, 256)),
                        'fake-embed', '1024', 1024)
                RETURNING unit_id
                """,
                tenant_id,
                doc_id,
                project_id,
                out["speakers"]["mod"],
                q_seq,
                float(q_seq),
                question,
                sha_q,
                int(exchange_id),
                _sql_vec(vec),
            )
            a_id = await conn.fetchval(
                """
                INSERT INTO research_units
                (tenant_id, doc_id, project_id, speaker_id, seq, t_start, text, text_sha256,
                 role_in_exchange, exchange_id, is_codable, embedding, embedding_256,
                 embedding_model, model_revision, dimensions)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'answer', $9, TRUE,
                        $10::vector, l2_normalize(subvector($10::vector, 1, 256)),
                        'fake-embed', '1024', 1024)
                RETURNING unit_id
                """,
                tenant_id,
                doc_id,
                project_id,
                out["speakers"][speaker],
                a_seq,
                float(a_seq),
                _unit_text(theme, idx, text),
                sha_a,
                int(exchange_id),
                _sql_vec(vec),
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
            out["units"][theme] = int(a_id)
            out["shas"][theme] = sha_a
    return out


async def _seed_codebook(pool: Any, tenant_id: str, seed: dict[str, Any]) -> dict[str, str]:
    """Two codes: A (close gold geometry), B (near-A centroid for the overlap flag)."""
    from corpus_kb.storage.tenant_conn import tenant_connection

    version_id = str(uuid4())
    code_a = str(uuid4())
    code_b = str(uuid4())
    code_c = str(uuid4())
    async with tenant_connection(pool, tenant_id) as conn:
        await conn.execute(
            """
            INSERT INTO codebook_versions (version_id, tenant_id, label, sha256, paradigm)
            VALUES ($1, $2, 'v1', $3, 'deductive')
            """,
            version_id,
            tenant_id,
            uuid4().hex,
        )
        for code_id, name, gold in (
            (code_a, "billing-migration", [seed["shas"]["A"], seed["shas"]["A2"]]),
            (code_b, "scheduling-desk", [seed["shas"]["B"], seed["shas"]["B2"]]),
            (code_c, "never-assigned", []),
        ):
            await conn.execute(
                """
                INSERT INTO code_registry
                (code_id, tenant_id, codebook_version_id, name, brief_definition,
                 inclusion_criteria, exclusion_criteria, theory)
                VALUES ($1, $2, $3, $4, $5, 'inclusion', 'exclusion', $6::jsonb)
                """,
                code_id,
                tenant_id,
                version_id,
                name,
                f"definition for {name}",
                json.dumps({"exemplar_text_sha256": gold}),
            )
    return {"version_id": version_id, "A": code_a, "B": code_b, "C": code_c}


async def _assign(
    pool: Any,
    tenant_id: str,
    unit_id: int,
    code_id: str,
    version_id: str,
    *,
    confidence: str = "high",
    status: str = "auto",
    sim: float = 0.95,
    basis: str = "explicit_in_answer",
    term_origin: str = "participant",
) -> int:
    from corpus_kb.storage.tenant_conn import tenant_connection

    async with tenant_connection(pool, tenant_id) as conn:
        return int(
            await conn.fetchval(
                """
                INSERT INTO research_assignments
                (tenant_id, assignment_aggregate_id, unit_id, code_id, cb_version_id,
                 sim_answer, sim_qa, sim_q, evidence_basis, stance, term_origin,
                 rationale, confidence, tier_fired, status)
                VALUES ($1, $2, $3, $4, $5, $6, $6, $6, $7, 'affirm', $8,
                        'deductive:v2:explicit', $9, 0, $10)
                RETURNING assignment_id
                """,
                tenant_id,
                str(uuid4()),
                unit_id,
                code_id,
                version_id,
                sim,
                basis,
                term_origin,
                confidence,
                status,
            )
        )


@pytest.fixture
async def seeded(research_pool):
    project = str(uuid4())
    seed = await _seed(research_pool, NOTEBOOK_TENANT, project)
    codes = await _seed_codebook(research_pool, NOTEBOOK_TENANT, seed)
    # A: one high explicit + one moderator-introduced + one disagreement unit
    await _assign(
        research_pool, NOTEBOOK_TENANT, seed["units"]["A"], codes["A"], codes["version_id"]
    )
    await _assign(
        research_pool,
        NOTEBOOK_TENANT,
        seed["units"]["A2"],
        codes["A"],
        codes["version_id"],
        term_origin="moderator",
    )
    # B: high + low-confidence + one double-assigned (borderline margin) + one review
    await _assign(
        research_pool, NOTEBOOK_TENANT, seed["units"]["B"], codes["B"], codes["version_id"]
    )
    await _assign(
        research_pool,
        NOTEBOOK_TENANT,
        seed["units"]["B2"],
        codes["B"],
        codes["version_id"],
        confidence="low",
        sim=0.6,
    )
    await _assign(
        research_pool,
        NOTEBOOK_TENANT,
        seed["units"]["B2"],
        codes["A"],
        codes["version_id"],
        sim=0.58,
    )
    await _assign(
        research_pool,
        NOTEBOOK_TENANT,
        seed["units"]["B"],
        codes["B"],
        codes["version_id"],
        status="review",
        sim=0.4,
        basis="question_dependent",
    )
    async with research_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", NOTEBOOK_TENANT)
        await conn.execute(
            """
            INSERT INTO research_signals (tenant_id, unit_id, tier, semantic_entropy, n_clusters)
            VALUES ($1, $2, 3, 0.9, 2)
            """,
            NOTEBOOK_TENANT,
            seed["units"]["A"],
        )
        await conn.execute(
            """
            INSERT INTO research_runs (run_id, tenant_id, state, checkpoint)
            VALUES ($1, $2, 'stopped', $3::jsonb)
            """,
            str(uuid4()),
            NOTEBOOK_TENANT,
            json.dumps({"conformal": {"per_unit": {str(seed["units"]["B2"]): 2}}}),
        )
    return {"project": project, "seed": seed, "codes": codes}


class FakeLlm:
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages: object, model: object = None, options: object = None) -> object:
        self.calls += 1
        return {"message": {"content": "unused"}}


async def test_ask_retrieval_only_zero_llm_calls_with_citations(research_pool, seeded) -> None:
    llm = FakeLlm()
    result = await notebook_ask(
        research_pool,
        _embedder(research_pool),
        llm,
        NotebookQuery(
            question="billing migration finished friday",
            tenant_id=UUID(NOTEBOOK_TENANT),
            project_id=UUID(seeded["project"]),
            k=3,
            retrieval_only=True,
        ),
    )
    assert llm.calls == 0, "retrieval-only ask must make ZERO LLM calls"
    assert result["status"] == "evidence"
    evidence = cast("list[dict[str, object]]", result["evidence"])
    assert evidence, "lexical-exact query returned no citations"
    top = evidence[0]
    assert top["n"] == 1
    assert top["title"] == "notebook-fixture"
    assert top["exchange_seq"] is not None
    assert top["speaker"] in ("R1", "MOD")
    assert top["timestamp_s"] is not None
    assert "question" in top and "answer" in top


async def test_ask_code_filter_no_assignments_gives_clear_empty(research_pool, seeded) -> None:
    result = await notebook_ask(
        research_pool,
        _embedder(research_pool),
        FakeLlm(),
        NotebookQuery(
            question="anything at all",
            tenant_id=UUID(NOTEBOOK_TENANT),
            project_id=UUID(seeded["project"]),
            code="never-assigned",
        ),
    )
    assert result["status"] == "empty"
    assert "no evidence matches" in str(result["message"])


async def test_ask_code_filter_scopes_evidence(research_pool, seeded) -> None:
    tenant = UUID(NOTEBOOK_TENANT)
    project = UUID(seeded["project"])
    unscoped = await notebook_ask(
        research_pool,
        _embedder(research_pool),
        FakeLlm(),
        NotebookQuery(
            question="scheduling desk reassigned technicians",
            tenant_id=tenant,
            project_id=project,
        ),
    )
    assert unscoped["status"] == "evidence"
    scoped = await notebook_ask(
        research_pool,
        _embedder(research_pool),
        FakeLlm(),
        NotebookQuery(
            question="scheduling desk reassigned technicians",
            tenant_id=tenant,
            project_id=project,
            code="scheduling-desk",
        ),
    )
    assert scoped["status"] == "evidence"
    # Every evidence unit carries a scheduling-desk assignment; the billing
    # unit1 (unassigned to scheduling-desk) must be excluded.
    for item in cast("list[dict[str, object]]", scoped["evidence"]):
        assert "unit1" not in str(item["answer"]), (
            "billing unit leaked through the scheduling-desk code filter"
        )
    cross = await notebook_ask(
        research_pool,
        _embedder(research_pool),
        FakeLlm(),
        NotebookQuery(
            question="billing migration finished friday",
            tenant_id=tenant,
            project_id=project,
            code="scheduling-desk",
        ),
    )
    # The filter contract: whatever retrieval surfaces, only units holding a
    # scheduling-desk assignment (units 3/4) may appear.
    if cross["status"] == "evidence":
        for item in cast("list[dict[str, object]]", cross["evidence"]):
            answer = str(item["answer"])
            assert "unit3" in answer or "unit4" in answer, (
                f"unit without a scheduling-desk assignment leaked: {answer[:80]}"
            )


async def test_ask_min_confidence_excludes_low_units(research_pool, seeded) -> None:
    tenant = UUID(NOTEBOOK_TENANT)
    project = UUID(seeded["project"])
    result = await notebook_ask(
        research_pool,
        _embedder(research_pool),
        FakeLlm(),
        NotebookQuery(
            question="scheduling desk reassigned technicians",
            tenant_id=tenant,
            project_id=project,
            code="scheduling-desk",
            min_confidence="high",
        ),
    )
    assert result["status"] == "evidence"
    for item in cast("list[dict[str, object]]", result["evidence"]):
        assert "unit4" not in str(item["answer"]), (
            "low-confidence assignment leaked through the --min-confidence high filter"
        )


async def test_evidence_view_tabs_flags_and_conformal(research_pool, seeded) -> None:
    result = await evidence_for_code(research_pool, UUID(NOTEBOOK_TENANT), "billing-migration")
    assert result["status"] == "ok"
    tabs = cast("dict[str, list[dict[str, object]]]", result["tabs"])
    assert len(tabs["explicit"]) >= 1
    by_unit: dict[int, dict[str, object]] = {}
    for tab in ("explicit", "question_dependent"):
        for entry in tabs[tab]:
            by_unit[int(entry["unit_id"])] = entry
    unit_a = seeded["seed"]["units"]["A"]
    unit_a2 = seeded["seed"]["units"]["A2"]
    assert unit_a in by_unit and unit_a2 in by_unit
    assert by_unit[unit_a]["disagreement"] is True
    assert by_unit[unit_a2]["moderator_introduced"] is True
    for entry in by_unit.values():
        assert int(entry["conformal_set_size"]) >= 1


async def test_evidence_view_conformal_set_size_from_run(research_pool, seeded) -> None:
    result = await evidence_for_code(research_pool, UUID(NOTEBOOK_TENANT), "scheduling-desk")
    assert result["status"] == "ok"
    by_unit = {
        int(e["unit_id"]): e
        for tab in ("explicit", "question_dependent")
        for e in cast("list[dict[str, object]]", result["tabs"])[tab]
    }
    assert by_unit[seeded["seed"]["units"]["B2"]]["conformal_set_size"] == 2
    assert by_unit[seeded["seed"]["units"]["B"]]["conformal_set_size"] == 1


async def test_uncoded_view_surfaces_only_unassigned(research_pool, seeded) -> None:
    result = await uncoded_units(
        research_pool, _embedder(research_pool), UUID(NOTEBOOK_TENANT), k=40
    )
    assert result["status"] == "ok"
    ids = [int(u["unit_id"]) for u in cast("list[dict[str, object]]", result["units"])]
    assert seeded["seed"]["units"]["H"] in ids
    assert seeded["seed"]["units"]["H2"] in ids
    assert seeded["seed"]["units"]["A"] not in ids
    margins = [float(u["margin"]) for u in cast("list[dict[str, object]]", result["units"])]
    assert margins == sorted(margins, reverse=True), "units must rank by margin descending"


async def test_overlap_view_flags_near_parallel_codes(research_pool, seeded) -> None:
    result = await overlap_view(research_pool, UUID(NOTEBOOK_TENANT))
    assert result["status"] == "ok"
    pairs = [(p["a"], p["b"]) for p in cast("list[dict[str, object]]", result["flagged_pairs"])]
    assert pairs, "near-parallel A/B centroids must flag"
    borderline_ids = {
        int(u["unit_id"]) for u in cast("list[dict[str, object]]", result["borderline_units"])
    }
    assert seeded["seed"]["units"]["B2"] in borderline_ids, (
        "double-assigned unit with top-2 margin 0.02 must surface as borderline"
    )


def _cli_project(research_dsn: str) -> str:
    """Seed a minimal corpus under the CLI's default tenant, synchronously."""

    async def _prepare() -> str:
        import asyncpg

        pool = await asyncpg.create_pool(research_dsn)
        try:
            project = str(uuid4())
            await _seed(pool, CLI_TENANT, project)
            return project
        finally:
            await pool.close()

    import asyncio

    return asyncio.run(_prepare())


def test_cli_ask_demo_cited_evidence(research_dsn, monkeypatch) -> None:
    """The CLI demo: one question, exchange-level citations, in-process."""
    from typer.testing import CliRunner

    from corpus_kb.cli import app

    monkeypatch.setenv("CORPUS_KB_DATABASE_URL", research_dsn)
    project = _cli_project(research_dsn)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "research",
            "ask",
            "billing migration finished friday",
            "--project-id",
            project,
            "--retrieval-only",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "evidence"
    assert payload["llm_calls"] == 0
    assert payload["evidence"], "CLI demo returned no citations"
    top = payload["evidence"][0]
    assert top["exchange_seq"] is not None
    assert top["highlighted_answer"]


def test_cli_ask_unknown_code_exits_clean_with_message(research_dsn, monkeypatch) -> None:
    from typer.testing import CliRunner

    from corpus_kb.cli import app

    monkeypatch.setenv("CORPUS_KB_DATABASE_URL", research_dsn)
    project = _cli_project(research_dsn)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "research",
            "ask",
            "anything",
            "--project-id",
            project,
            "--code",
            "missing-code-name",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "NO EVIDENCE" in result.output
    assert "unknown code" in result.output
