"""Grounded answer composition: retrieval, generation, citations, verify.

Generalizes the notebook pipeline's shape (``research/notebook.py``) to
the whole knowledge base: hybrid retrieval, numbered evidence blocks,
temperature-0 generation with [n] citations, sentence-level citation
filtering, and optional claim verification via the config-driven judge.

Every step on the way to an answer is an HONEST envelope, never a raise:

  * no evidence          -> status ``empty`` (no LLM call is made);
  * generation failure   -> status ``degraded`` with the evidence list
                            and a ``note`` (the model is down or answered
                            nothing citable — the evidence still ships);
  * no judge / no claims -> the verifier ``abstains`` (groundedness 0.0
                            with empty verdicts), never a fake score.

The same :class:`AnswerBundle` payload reaches all four surfaces:
``tools/ask_tools.py`` is the ONE function they route through.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, cast
from uuid import UUID

from corpus_kb.contracts.model import DEFAULT_TENANT_ID
from corpus_kb.domain.models import SearchQuery, SearchResult
from corpus_kb.handlers.query_integrity import SearchEnvelope
from corpus_kb.research.notebook import cite_sentences

# The honest-status vocabulary for the ask operation.
STATUS_SUCCESS = "success"
STATUS_EMPTY = "empty"
STATUS_DEGRADED = "degraded"

# Notes carried in ``result.note`` when the answer degrades (<= 20 words).
GENERATION_UNAVAILABLE = "generation unavailable; returning evidence only"
NO_CITABLE_SENTENCES = "no citable sentences produced; returning evidence only"
NO_EVIDENCE = "no evidence matched the question"

ASK_SYSTEM_PROMPT = (
    "You answer questions STRICTLY from the numbered evidence excerpts "
    "provided. End EVERY sentence with the evidence index in square "
    "brackets, e.g. [1] or [2,3]. Never write a sentence without a "
    "citation. Never use outside knowledge. If the evidence is "
    "insufficient, say so and cite the closest excerpt."
)

# The canonical placeholder tenant as a UUID (B008: a module-level default,
# never a call in an argument default).
DEFAULT_TENANT_UUID = UUID(DEFAULT_TENANT_ID)


class SearchHandler(Protocol):
    """The retrieval seam: exactly what ``compose_answer`` needs."""

    async def handle_search_with_meta(self, query: SearchQuery) -> SearchEnvelope: ...


@dataclass(frozen=True)
class AnswerBundle:
    """The ask operation's outcome, serialized identically on every surface."""

    status: str
    answer: str
    sentences: list[dict[str, object]]
    sources: list[dict[str, object]]
    verify: dict[str, object]
    model: str
    note: str | None
    degraded_excluded: dict[str, object]

    def to_payload(self) -> dict[str, object]:
        """The uniform envelope: ``{status, result, degraded_excluded}``."""
        return {
            "status": self.status,
            "result": {
                "answer": self.answer,
                "sentences": [dict(sentence) for sentence in self.sentences],
                "sources": [dict(source) for source in self.sources],
                "verify": dict(self.verify),
                "model": self.model,
                "note": self.note,
            },
            "degraded_excluded": dict(self.degraded_excluded),
        }


def evidence_blocks(results: list[SearchResult]) -> list[dict[str, object]]:
    """Numbered evidence blocks: chunk id, source, text, score, provenance."""
    return [
        {
            "n": index + 1,
            "chunk_id": str(result.chunk_id),
            "doc_id": str(result.doc_id),
            "source": result.source,
            "text": result.text,
            "score": result.score,
            "file_path": result.file_path,
            "heading_path": list(result.heading_path) if result.heading_path else [],
        }
        for index, result in enumerate(results)
    ]


def _abstain() -> dict[str, object]:
    """The honest abstention verdict set (no judge, or nothing to check)."""
    return {"groundedness": 0.0, "verdicts": [], "abstained": True}


def _model_name(llm: object) -> str:
    return str(getattr(llm, "model", "") or "")


def _build_prompt(question: str, sources: list[dict[str, object]]) -> str:
    blocks = [f"[{src['n']}] ({src['source']})\n{str(src['text']).strip()}" for src in sources]
    return (
        f"Question: {question}\n\nEvidence:\n" + "\n\n".join(blocks) + "\n\n"
        "Answer the question using ONLY this evidence. Every sentence ends "
        "with its evidence index like [1]."
    )


async def _generate(
    llm: object, question: str, sources: list[dict[str, object]]
) -> tuple[str, str, bool]:
    """One temperature-0 chat call. Returns ``(text, model, failed)``.

    ``failed`` covers a missing/broken chat seam, an error dict (the
    LlmHandler degrade contract), and an empty response — the caller
    degrades to evidence-only in every case.
    """
    model = _model_name(llm)
    raw_chat = getattr(llm, "chat", None)
    if not callable(raw_chat):
        return "", model, True
    chat = cast("Callable[..., Awaitable[object]]", raw_chat)
    try:
        response = await chat(
            [
                {"role": "system", "content": ASK_SYSTEM_PROMPT},
                {"role": "user", "content": _build_prompt(question, sources)},
            ],
            options={"temperature": 0},
        )
    except Exception:
        # The degrade contract: a broken seam is a note, never a raise.
        return "", model, True
    text = ""
    if isinstance(response, dict):
        if response.get("error"):
            reported = str(response.get("model", "") or "")
            return "", reported or model, True
        message = response.get("message")
        if isinstance(message, dict):
            text = str(message.get("content", "") or "")
    return text, model, not text.strip()


def verify_claims(
    judge: object, answer: str, sources: list[dict[str, object]]
) -> dict[str, object]:
    """Decompose + entail per claim; abstain without a judge or claims.

    Mirrors the judge's own never-raises contract: a broken judge yields
    an abstention, never a fabricated groundedness score.
    """
    if judge is None:
        return _abstain()
    decompose = getattr(judge, "decompose", None)
    entail = getattr(judge, "entail", None)
    if not callable(decompose) or not callable(entail):
        return _abstain()
    entail_fn = cast("Callable[..., tuple[str, float]]", entail)
    try:
        claims = decompose(answer)
    except Exception:
        return _abstain()
    if not isinstance(claims, list) or not claims:
        return _abstain()
    cited = [str(source["text"]) for source in sources]
    verdicts: list[dict[str, object]] = []
    for claim in claims:
        try:
            label, confidence = entail_fn(str(claim), cited)
        except Exception:
            label, confidence = "unsupported", 0.0
        verdicts.append({"claim": str(claim), "label": str(label), "confidence": float(confidence)})
    entailed = sum(1 for verdict in verdicts if verdict["label"] == "entailed")
    return {
        "groundedness": entailed / len(verdicts),
        "verdicts": verdicts,
        "abstained": False,
    }


async def compose_answer(
    question: str,
    handler: SearchHandler,
    llm: object,
    judge: object | None = None,
    tenant_id: UUID = DEFAULT_TENANT_UUID,
    k: int | None = None,
) -> AnswerBundle:
    """Retrieve, generate with citations, filter sentences, verify claims.

    Retrieval goes through ``handle_search_with_meta`` so the honest
    ``degraded_excluded`` record rides along on every outcome. The answer
    carries ONLY sentences whose [n] index resolves to retrieved evidence
    (the grounded contract); everything else is dropped.
    """
    from corpus_kb.utils.tracing import (
        ATTR_CLAIMS,
        ATTR_GROUNDEDNESS,
        ATTR_MODEL,
        ATTR_ROW_COUNT,
        SPAN_ASK_GENERATE,
        SPAN_ASK_RETRIEVE,
        SPAN_ASK_VERIFY,
        span,
    )

    with span(SPAN_ASK_RETRIEVE) as current:
        envelope = await handler.handle_search_with_meta(
            SearchQuery(tenant_id=tenant_id, query=question, k=k)
        )
        current.set_attribute(ATTR_ROW_COUNT, len(envelope.results))
    sources = evidence_blocks(envelope.results)
    degraded = envelope.degraded_excluded.to_dict()
    model = _model_name(llm)
    if not sources:
        return AnswerBundle(STATUS_EMPTY, "", [], sources, _abstain(), model, NO_EVIDENCE, degraded)
    with span(SPAN_ASK_GENERATE) as current:
        text, gen_model, failed = await _generate(llm, question, sources)
        current.set_attribute(ATTR_MODEL, gen_model)
    model = gen_model or model
    if failed:
        return AnswerBundle(
            STATUS_DEGRADED, "", [], sources, _abstain(), model, GENERATION_UNAVAILABLE, degraded
        )
    sentences = cite_sentences(text, len(sources))
    if not sentences:
        return AnswerBundle(
            STATUS_DEGRADED, "", [], sources, _abstain(), model, NO_CITABLE_SENTENCES, degraded
        )
    answer = " ".join(str(sentence["text"]) for sentence in sentences)
    with span(SPAN_ASK_VERIFY) as current:
        verify = verify_claims(judge, answer, sources)
        verdicts = verify.get("verdicts")
        groundedness = verify.get("groundedness")
        current.set_attribute(ATTR_CLAIMS, len(verdicts) if isinstance(verdicts, list) else 0)
        current.set_attribute(
            ATTR_GROUNDEDNESS,
            float(groundedness) if isinstance(groundedness, (int, float)) else 0.0,
        )
    return AnswerBundle(
        STATUS_SUCCESS,
        answer,
        sentences,
        sources,
        verify,
        model,
        None,
        degraded,
    )
