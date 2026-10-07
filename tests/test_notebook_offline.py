"""Offline notebook-surface tests (todo 18): the grounded-citation contract,
retrieval-only zero-LLM guarantee (mock-asserted), degraded generation, the
empty-evidence QA scenario, and the retrieval assignment-filter SQL shape.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from corpus_kb.research.notebook import (
    NotebookQuery,
    cite_sentences,
    notebook_ask,
    validate_notebook_filters,
)
from corpus_kb.research.retrieval import (
    CONFIDENCE_FLOORS,
    Citation,
    ResearchQuery,
    _Sql,
    _unit_filters,
)

TENANT = UUID("00000000-0000-0000-0000-000000000002")
PROJECT = uuid4()


def _citation(answer: str = "the migration finished on Friday", seq: int = 1) -> Citation:
    return Citation(
        doc_id=uuid4(),
        doc_title="interview-3",
        project_name="billing",
        exchange_seq=seq,
        question_text="What happened on Friday?",
        answer_text=answer,
        answer_highlight=(4, 13),
        matched_kind="answer",
        score=0.9,
        unit_seqs=(2 * seq + 1,),
    )


class FakeSearch:
    def __init__(self, results: list[Citation]) -> None:
        self.results = results
        self.queries: list[ResearchQuery] = []

    async def __call__(self, pool: object, embedder: object, q: ResearchQuery) -> list[Citation]:
        self.queries.append(q)
        return self.results


class FakeLlm:
    def __init__(self, reply: str | None, error: bool = False) -> None:
        self.reply = reply
        self.error = error
        self.calls = 0

    async def chat(self, messages: object, model: object = None, options: object = None) -> object:
        self.calls += 1
        if self.error:
            return {"error": "connection refused", "model": "fake"}
        return {"message": {"content": self.reply}}


def _query(**overrides: object) -> NotebookQuery:
    defaults: dict[str, object] = {
        "question": "when did the migration finish?",
        "tenant_id": TENANT,
        "project_id": PROJECT,
    }
    defaults.update(overrides)
    return NotebookQuery(**defaults)  # type: ignore[arg-type]


def test_cite_sentences_keeps_only_cited() -> None:
    text = "It finished Friday [1]. No source for this. Duplicate refs work [2, 1]!"
    sentences = cite_sentences(text, 2)
    assert [s["text"] for s in sentences] == [
        "It finished Friday [1].",
        "Duplicate refs work [2, 1]!",
    ]
    assert sentences[0]["citations"] == [1]
    assert sentences[1]["citations"] == [1, 2]


def test_cite_sentences_drops_out_of_range_refs() -> None:
    assert cite_sentences("Claims the impossible [7].", 2) == []


def test_cite_sentences_empty_answer() -> None:
    assert cite_sentences("", 3) == []
    assert cite_sentences("no citations at all here", 3) == []


def test_validate_notebook_filters_rejects_unknown_values() -> None:
    assert validate_notebook_filters("HIGH", None) is not None
    assert validate_notebook_filters(None, "maybe") is not None
    assert validate_notebook_filters("medium", "review") is None
    assert set(CONFIDENCE_FLOORS) == {"high", "medium", "low"}


def _stub_evidence(monkeypatch: object) -> None:
    """Offline stand-in for enrich_citations (DB-bound; covered in test_notebook_db)."""
    import corpus_kb.research.notebook as nb

    async def _enrich(pool: object, tenant_id: object, citations: list) -> list[dict]:
        return [
            {
                "n": i + 1,
                "title": c.doc_title,
                "exchange_seq": c.exchange_seq,
                "speaker": "R1",
                "timestamp_s": 12.0,
                "question": c.question_text,
                "answer": c.answer_text,
                "highlighted_answer": c.highlighted_answer(),
            }
            for i, c in enumerate(citations)
        ]

    monkeypatch.setattr(nb, "enrich_citations", _enrich)


async def test_retrieval_only_makes_zero_llm_calls(monkeypatch: object) -> None:
    fake = FakeSearch([_citation()])
    import corpus_kb.research.notebook as nb

    monkeypatch.setattr(nb, "research_search", fake)
    _stub_evidence(monkeypatch)
    llm = FakeLlm("ignored")
    result = await notebook_ask(object(), object(), llm, _query(retrieval_only=True))
    assert llm.calls == 0, "retrieval-only mode must perform zero LLM calls"
    assert result["status"] == "evidence"
    assert result["llm_calls"] == 0
    evidence = list(result["evidence"])
    assert evidence and evidence[0]["n"] == 1
    assert evidence[0]["exchange_seq"] is not None
    assert evidence[0]["title"] == "interview-3"


async def test_generation_attaches_citations_and_drops_uncited(monkeypatch: object) -> None:
    fake = FakeSearch([_citation(seq=1), _citation(seq=2)])
    import corpus_kb.research.notebook as nb

    monkeypatch.setattr(nb, "research_search", fake)
    _stub_evidence(monkeypatch)
    llm = FakeLlm("It finished Friday [1]. Invented claim without source. Confirmed [2].")
    result = await notebook_ask(object(), object(), llm, _query())
    assert llm.calls == 1
    assert result["status"] == "answer"
    sentences = list(result["sentences"])
    assert [s["text"] for s in sentences] == [
        "It finished Friday [1].",
        "Confirmed [2].",
    ]
    assert sentences[0]["citations"] == [1]
    assert sentences[1]["citations"] == [2]


async def test_llm_failure_degrades_to_evidence(monkeypatch: object) -> None:
    fake = FakeSearch([_citation()])
    import corpus_kb.research.notebook as nb

    monkeypatch.setattr(nb, "research_search", fake)
    _stub_evidence(monkeypatch)
    llm = FakeLlm(None, error=True)
    result = await notebook_ask(object(), object(), llm, _query())
    assert llm.calls == 1
    assert result["status"] == "evidence"
    assert "generation unavailable" in str(result["note"])


async def test_empty_evidence_returns_clear_message_not_error(monkeypatch: object) -> None:
    fake = FakeSearch([])
    import corpus_kb.research.notebook as nb

    monkeypatch.setattr(nb, "research_search", fake)
    llm = FakeLlm("unused")
    result = await notebook_ask(object(), object(), llm, _query())
    assert result["status"] == "empty"
    assert "no evidence matches" in str(result["message"])
    assert llm.calls == 0


async def test_unknown_code_returns_empty_with_message(monkeypatch: object) -> None:
    import corpus_kb.research.notebook as nb

    class NoPool:
        pass

    async def _fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("resolve_code must not hit the DB for an unresolvable name")

    class FakeResolve:
        async def __call__(self, pool: object, tenant: object, code: str) -> None:
            return None

    monkeypatch.setattr(nb, "resolve_code", FakeResolve())
    result = await notebook_ask(NoPool(), object(), FakeLlm(None), _query(code="ghost"))
    assert result["status"] == "empty"
    assert "ghost" in str(result["message"])


async def test_invalid_filter_short_circuits_before_search(monkeypatch: object) -> None:
    fake = FakeSearch([])
    import corpus_kb.research.notebook as nb

    monkeypatch.setattr(nb, "research_search", fake)
    result = await notebook_ask(object(), object(), FakeLlm(None), _query(min_confidence="HIGH"))
    assert result["status"] == "error"
    assert fake.queries == [], "invalid filters must not reach retrieval"


def test_unit_filters_build_assignment_subquery() -> None:
    sql = _Sql(str(TENANT), str(PROJECT))
    q = ResearchQuery(
        query="x",
        tenant_id=TENANT,
        project_id=PROJECT,
        code_id="abc-123",
        min_confidence="medium",
        review_status="confirmed",
    )
    frag = _unit_filters(sql, q)
    assert "research_assignments" in frag
    assert "ra.code_id = $3" in frag
    assert "ra.confidence IN ($4, $5)" in frag
    assert "ra.status = $6" in frag
    assert "ra.tenant_id = u.tenant_id" in frag
    assert len(sql.values) == 6
    assert sql.values[2] == "abc-123"
    assert sql.values[3] == "high"
    assert sql.values[4] == "medium"
    assert sql.values[5] == "confirmed"


def test_exchange_filters_use_exists_over_answer_units() -> None:
    from corpus_kb.research.retrieval import _exchange_filters

    sql = _Sql(str(TENANT), str(PROJECT))
    q = ResearchQuery(query="x", tenant_id=TENANT, project_id=PROJECT, code_id="abc-123")
    ex_frag = _exchange_filters(sql, q)
    assert "EXISTS (SELECT 1 FROM research_assignments ra" in ex_frag
    assert "ra.unit_id = ANY(e.a_unit_ids)" in ex_frag
