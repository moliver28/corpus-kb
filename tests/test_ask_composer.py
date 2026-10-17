"""Pure composer tests for the ask operation (offline; no Ollama, no DB).

The composer runs against fake objects at its real seams: a stub handler
returning canned ``SearchEnvelope`` values, a stub LLM with an async
``chat``, and a stub judge with ``decompose``/``entail``. The exact
envelope key-set is pinned once (house style) so surface drift fails
here first.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from corpus_kb.ask.composer import (
    NO_CITABLE_SENTENCES,
    NO_EVIDENCE,
    STATUS_DEGRADED,
    STATUS_EMPTY,
    STATUS_SUCCESS,
    compose_answer,
)
from corpus_kb.domain.models import SearchResult
from corpus_kb.handlers.query_integrity import DegradedExcluded, SearchEnvelope
from corpus_kb.research.notebook import cite_sentences

ENVELOPE_KEYS = {"status", "result", "degraded_excluded"}
RESULT_KEYS = {"answer", "sentences", "sources", "verify", "model", "note"}
ABSTAINED = {"groundedness": 0.0, "verdicts": [], "abstained": True}


def _result(text: str, score: float = 0.9) -> SearchResult:
    return SearchResult(chunk_id=uuid4(), text=text, score=score, source="doc.txt", doc_id=uuid4())


def _envelope(
    results: list[SearchResult], reasons: list[str] | None = None, count: int = 0
) -> SearchEnvelope:
    return SearchEnvelope(
        status="success",
        results=results,
        degraded_excluded=DegradedExcluded(count=count, reasons=list(reasons or [])),
    )


class _FakeHandler:
    """Returns a canned envelope; records the SearchQuery it received."""

    def __init__(self, envelope: SearchEnvelope) -> None:
        self.envelope = envelope
        self.queries: list[object] = []

    async def handle_search_with_meta(self, query: object) -> SearchEnvelope:
        self.queries.append(query)
        return self.envelope


class _FakeLlm:
    """Canned chat responses; records every call (messages + options)."""

    model = "fake-model"

    def __init__(
        self,
        text: str | None = None,
        error: str | None = None,
        broken: bool = False,
    ) -> None:
        self.text = text
        self.error = error
        self.broken = broken
        self.calls: list[dict[str, object]] = []

    async def chat(
        self, messages: list[dict[str, str]], model: str | None = None, options: object = None
    ) -> dict[str, object]:
        self.calls.append({"messages": messages, "options": options})
        if self.broken:
            raise RuntimeError("chat exploded")
        if self.error is not None:
            return {"error": self.error, "model": self.model}
        return {"message": {"content": self.text}}


class _FakeJudge:
    def __init__(
        self, claims: list[str], labels: dict[str, str] | None = None, confidence: float = 0.9
    ) -> None:
        self.claims = claims
        self.labels = labels or {}
        self.confidence = confidence
        self.entail_args: list[tuple[str, list[str]]] = []

    def decompose(self, answer: str) -> list[str]:
        return list(self.claims)

    def entail(self, claim: str, cited_texts: list[str]) -> tuple[str, float]:
        self.entail_args.append((claim, list(cited_texts)))
        return self.labels.get(claim, "entailed"), self.confidence


# ---------------------------------------------------------------------------
# Success + the pinned envelope
# ---------------------------------------------------------------------------


async def test_success_filters_uncited_sentences_and_pins_the_envelope() -> None:
    handler = _FakeHandler(_envelope([_result("alpha evidence"), _result("beta evidence")]))
    llm = _FakeLlm(text="Alpha holds [1]. Beta holds too [2]. A sentence with no marker.")

    payload = (await compose_answer("q", handler, llm)).to_payload()

    assert set(payload) == ENVELOPE_KEYS
    assert set(payload["result"]) == RESULT_KEYS
    result = payload["result"]
    assert payload["status"] == STATUS_SUCCESS
    assert result["answer"] == "Alpha holds [1]. Beta holds too [2]."
    assert [s["citations"] for s in result["sentences"]] == [[1], [2]]
    assert result["model"] == "fake-model"
    assert result["note"] is None
    assert result["verify"] == ABSTAINED  # no judge -> honest abstention
    assert payload["degraded_excluded"] == {"count": 0, "reasons": []}


async def test_evidence_blocks_carry_provenance_and_one_based_indexes() -> None:
    handler = _FakeHandler(_envelope([_result("alpha"), _result("beta", score=0.5)]))
    llm = _FakeLlm(text="Alpha holds [1].")

    payload = (await compose_answer("q", handler, llm)).to_payload()

    sources = payload["result"]["sources"]
    assert [s["n"] for s in sources] == [1, 2]
    assert sources[0]["text"] == "alpha"
    assert sources[1]["score"] == 0.5
    for source in sources:
        assert set(source) >= {"n", "chunk_id", "doc_id", "source", "text", "score"}


async def test_query_and_k_reach_the_search_handler() -> None:
    from corpus_kb.domain.models import SearchQuery

    handler = _FakeHandler(_envelope([_result("alpha")]))
    llm = _FakeLlm(text="Alpha holds [1].")

    await compose_answer("q1", handler, llm, k=4)

    query = handler.queries[0]
    assert isinstance(query, SearchQuery)
    assert query.query == "q1"
    assert query.k == 4


# ---------------------------------------------------------------------------
# Honest degrade paths (never raise, never fabricate)
# ---------------------------------------------------------------------------


async def test_generation_error_degrades_to_evidence_with_note() -> None:
    handler = _FakeHandler(_envelope([_result("alpha")]))
    llm = _FakeLlm(error="connection refused")

    payload = (await compose_answer("q", handler, llm)).to_payload()

    assert payload["status"] == STATUS_DEGRADED
    assert payload["result"]["answer"] == ""
    assert payload["result"]["sentences"] == []
    assert [s["text"] for s in payload["result"]["sources"]] == ["alpha"]
    assert payload["result"]["note"] == "generation unavailable; returning evidence only"
    assert payload["result"]["verify"] == ABSTAINED


async def test_broken_chat_seam_degrades_without_raising() -> None:
    handler = _FakeHandler(_envelope([_result("alpha")]))
    llm = _FakeLlm(broken=True)

    payload = (await compose_answer("q", handler, llm)).to_payload()

    assert payload["status"] == STATUS_DEGRADED
    assert payload["result"]["sources"]


async def test_uncitable_generation_degrades_with_note() -> None:
    handler = _FakeHandler(_envelope([_result("alpha")]))
    llm = _FakeLlm(text="A fluent answer with zero citations.")

    payload = (await compose_answer("q", handler, llm)).to_payload()

    assert payload["status"] == STATUS_DEGRADED
    assert payload["result"]["note"] == NO_CITABLE_SENTENCES
    assert payload["result"]["verify"] == ABSTAINED


async def test_empty_evidence_is_empty_without_any_llm_call() -> None:
    handler = _FakeHandler(_envelope([]))
    llm = _FakeLlm(text="Alpha holds [1].")

    payload = (await compose_answer("q", handler, llm)).to_payload()

    assert payload["status"] == STATUS_EMPTY
    assert llm.calls == []
    assert payload["result"] == {
        "answer": "",
        "sentences": [],
        "sources": [],
        "verify": ABSTAINED,
        "model": "fake-model",
        "note": NO_EVIDENCE,
    }


async def test_degraded_excluded_rides_every_outcome() -> None:
    handler = _FakeHandler(_envelope([_result("alpha")], reasons=["zero-query-embedding"], count=3))
    llm = _FakeLlm(text="Alpha holds [1].")

    payload = (await compose_answer("q", handler, llm)).to_payload()

    assert payload["degraded_excluded"] == {"count": 3, "reasons": ["zero-query-embedding"]}


# ---------------------------------------------------------------------------
# Judge verification honesty
# ---------------------------------------------------------------------------


async def test_judge_verifies_claims_and_scores_groundedness() -> None:
    handler = _FakeHandler(_envelope([_result("alpha text"), _result("beta text")]))
    llm = _FakeLlm(text="Alpha holds [1]. Beta holds too [2].")
    judge = _FakeJudge(["alpha holds", "beta holds"], labels={"beta holds": "unsupported"})

    payload = (await compose_answer("q", handler, llm, judge=judge)).to_payload()

    verify = payload["result"]["verify"]
    assert verify["abstained"] is False
    assert verify["groundedness"] == 0.5
    assert [v["label"] for v in verify["verdicts"]] == ["entailed", "unsupported"]
    # Entailment sees the evidence texts, not the generated answer.
    assert judge.entail_args[0][1] == ["alpha text", "beta text"]


async def test_judge_zero_claims_abstains() -> None:
    handler = _FakeHandler(_envelope([_result("alpha")]))
    llm = _FakeLlm(text="Alpha holds [1].")
    judge = _FakeJudge([])

    payload = (await compose_answer("q", handler, llm, judge=judge)).to_payload()

    assert payload["result"]["verify"] == ABSTAINED


async def test_broken_judge_abstains_without_raising() -> None:
    class _ExplodingJudge:
        def decompose(self, answer: str) -> list[str]:
            raise RuntimeError("judge down")

    handler = _FakeHandler(_envelope([_result("alpha")]))
    llm = _FakeLlm(text="Alpha holds [1].")

    payload = (await compose_answer("q", handler, llm, judge=_ExplodingJudge())).to_payload()

    assert payload["result"]["verify"] == ABSTAINED


def test_cite_sentences_invalid_index_drops_whole_sentence() -> None:
    out = cite_sentences("Good [1]. Bad [5]. Also good [1,2].", 2)
    assert [s["citations"] for s in out] == [[1], [1, 2]]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("No refs here.", []),
        ("", []),
        ("One [1].", [[1]]),
    ],
)
def test_cite_sentences_edges(text: str, expected: list[list[int]]) -> None:
    out = cite_sentences(text, 1)
    assert [s["citations"] for s in out] == expected
