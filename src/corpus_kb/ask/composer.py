"""Grounded answer composition: retrieval, generation, citations, verify.

Generalizes the notebook pipeline's shape (``research/notebook.py``) to
the whole knowledge base: hybrid retrieval, numbered evidence blocks,
temperature-0 generation with [n] citations, sentence-level citation
filtering, and optional claim verification via the config-driven judge.
The shared steps (retrieval, citation rule, verification) live in
``ask/steps.py``; the streaming twin that reuses them is
``ask/streaming.py``.

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
from typing import cast
from uuid import UUID

from corpus_kb.ask.steps import (
    SearchHandler,
    abstain,
    evidence_blocks,
    retrieve_evidence,
    verify_claims,
    verify_with_span,
)
from corpus_kb.contracts.model import DEFAULT_TENANT_ID
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

__all__ = [
    "ASK_SYSTEM_PROMPT",
    "DEFAULT_TENANT_UUID",
    "GENERATION_UNAVAILABLE",
    "NO_CITABLE_SENTENCES",
    "NO_EVIDENCE",
    "STATUS_DEGRADED",
    "STATUS_EMPTY",
    "STATUS_SUCCESS",
    "AnswerBundle",
    "SearchHandler",
    "abstain",
    "build_prompt",
    "compose_answer",
    "evidence_blocks",
    "model_name",
    "retrieve_evidence",
    "verify_claims",
    "verify_with_span",
]


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
    # Issue #136: the multi-query rewrites actually used (the honest
    # receipt that fusion ran). Emitted under ``result`` only when set, so
    # the flag-off ask payload shape is unchanged.
    query_rewrites: list[str] | None = None

    def to_payload(self) -> dict[str, object]:
        """The uniform envelope: ``{status, result, degraded_excluded}``."""
        result: dict[str, object] = {
            "answer": self.answer,
            "sentences": [dict(sentence) for sentence in self.sentences],
            "sources": [dict(source) for source in self.sources],
            "verify": dict(self.verify),
            "model": self.model,
            "note": self.note,
        }
        if self.query_rewrites:
            result["query_rewrites"] = list(self.query_rewrites)
        return {
            "status": self.status,
            "result": result,
            "degraded_excluded": dict(self.degraded_excluded),
        }


def model_name(llm: object) -> str:
    return str(getattr(llm, "model", "") or "")


def build_prompt(question: str, sources: list[dict[str, object]]) -> str:
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
    model = model_name(llm)
    raw_chat = getattr(llm, "chat", None)
    if not callable(raw_chat):
        return "", model, True
    chat = cast("Callable[..., Awaitable[object]]", raw_chat)
    try:
        response = await chat(
            [
                {"role": "system", "content": ASK_SYSTEM_PROMPT},
                {"role": "user", "content": build_prompt(question, sources)},
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
    from corpus_kb.utils.tracing import ATTR_MODEL, SPAN_ASK_GENERATE, span

    envelope, sources, degraded = await retrieve_evidence(question, handler, tenant_id, k)
    # Issue #136 receipt: the multi-query rewrites, when the flag ran.
    rewrites = envelope.query_rewrites
    model = model_name(llm)
    if not sources:
        return AnswerBundle(
            STATUS_EMPTY, "", [], sources, abstain(), model, NO_EVIDENCE, degraded, rewrites
        )
    with span(SPAN_ASK_GENERATE) as current:
        text, gen_model, failed = await _generate(llm, question, sources)
        current.set_attribute(ATTR_MODEL, gen_model)
    model = gen_model or model
    if failed:
        return AnswerBundle(
            STATUS_DEGRADED,
            "",
            [],
            sources,
            abstain(),
            model,
            GENERATION_UNAVAILABLE,
            degraded,
            rewrites,
        )
    sentences = cite_sentences(text, len(sources))
    if not sentences:
        return AnswerBundle(
            STATUS_DEGRADED,
            "",
            [],
            sources,
            abstain(),
            model,
            NO_CITABLE_SENTENCES,
            degraded,
            rewrites,
        )
    answer = " ".join(str(sentence["text"]) for sentence in sentences)
    verify = await verify_with_span(judge, answer, sources)
    return AnswerBundle(
        STATUS_SUCCESS,
        answer,
        sentences,
        sources,
        verify,
        model,
        None,
        degraded,
        rewrites,
    )
