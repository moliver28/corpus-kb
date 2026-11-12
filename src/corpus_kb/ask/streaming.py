"""Streaming ask composition: delta events under the grounded contract.

The transport-friendly twin of :func:`corpus_kb.ask.composer.compose_answer`
(same pipeline: retrieve, generate, cite, verify). Generation streams token
pieces from ``LlmHandler.chat_stream``; every piece passes the SAME
sentence-level citation filter (``cite_sentences``), so each streamed
sentence is a grounded sentence and the joined stream equals the final
envelope's ``answer`` exactly.

Honesty rules match the batch composer: no evidence, a broken generation
seam, or an answer with no citable sentence yields NO delta frames and one
final envelope event — never a half-open stream. The generator always ends
with :class:`AskStreamFinal`, whose bundle carries the same
:class:`~corpus_kb.ask.composer.AnswerBundle` payload the batch path
returns on every surface.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from corpus_kb.ask.composer import (
    ASK_SYSTEM_PROMPT,
    DEFAULT_TENANT_UUID,
    GENERATION_UNAVAILABLE,
    NO_CITABLE_SENTENCES,
    NO_EVIDENCE,
    STATUS_DEGRADED,
    STATUS_EMPTY,
    STATUS_SUCCESS,
    AnswerBundle,
    build_prompt,
    model_name,
)
from corpus_kb.ask.steps import SearchHandler, abstain, retrieve_evidence, verify_with_span
from corpus_kb.research.notebook import _SENTENCE_SPLIT_RE, cite_sentences


@dataclass(frozen=True)
class AskStreamDelta:
    """Grounded answer text to append (one or more complete sentences)."""

    text: str


@dataclass(frozen=True)
class AskStreamFinal:
    """The terminal event: the complete ask envelope (AnswerBundle)."""

    bundle: AnswerBundle


AskStreamEvent = AskStreamDelta | AskStreamFinal


class GroundedEmitter:
    """The citation filter working on a token stream.

    Pieces buffer until a sentence boundary (the same split rule
    ``cite_sentences`` uses); a completed sentence ships only when its [n]
    markers all resolve to retrieved evidence. Join discipline matches the
    batch composer: kept sentences join with one space.
    """

    def __init__(self, n_evidence: int) -> None:
        self._n_evidence = n_evidence
        self._buffer = ""
        self._emitted = 0
        self.sentences: list[dict[str, object]] = []

    def feed(self, piece: str) -> str:
        """Consume one token piece; return grounded text now complete."""
        self._buffer += piece
        return self._drain(flush=False)

    def flush(self) -> str:
        """Consume the tail as a final sentence; return grounded text."""
        return self._drain(flush=True)

    def answer_text(self) -> str:
        """The kept sentences joined the way the batch composer joins them."""
        return " ".join(str(sentence["text"]) for sentence in self.sentences)

    def _drain(self, flush: bool) -> str:
        parts = _SENTENCE_SPLIT_RE.split(self._buffer)
        if len(parts) > 1:
            complete, self._buffer = parts[:-1], parts[-1]
        elif flush:
            complete, self._buffer = [self._buffer], ""
        else:
            return ""
        out = ""
        for raw in complete:
            sentence = raw.strip()
            if not sentence:
                continue
            kept = cite_sentences(sentence, self._n_evidence)
            if not kept:
                continue
            self.sentences.extend(kept)
            prefix = " " if self._emitted else ""
            out += prefix + str(kept[0]["text"])
            self._emitted += 1
        return out


async def compose_answer_stream(
    question: str,
    handler: SearchHandler,
    llm: object,
    judge: object | None = None,
    tenant_id: UUID = DEFAULT_TENANT_UUID,
    k: int | None = None,
) -> AsyncIterator[AskStreamEvent]:
    """Retrieve, stream cited deltas, then verify; end with the envelope.

    The events: zero or more :class:`AskStreamDelta` (only grounded
    sentences, in answer order), then exactly one :class:`AskStreamFinal`.
    A generation seam that fails mid-stream ends the stream honestly: the
    deltas already sent stay, and the final event carries the degraded
    envelope with the evidence — never a fake completion.
    """
    from corpus_kb.utils.tracing import ATTR_MODEL, SPAN_ASK_GENERATE, span

    envelope, sources, degraded = await retrieve_evidence(question, handler, tenant_id, k)
    # Issue #136 receipt: the multi-query rewrites, when the flag ran
    # (the streamed envelope carries the same receipt as the batch one).
    rewrites = envelope.query_rewrites
    model = model_name(llm)
    if not sources:
        yield AskStreamFinal(
            AnswerBundle(
                STATUS_EMPTY, "", [], sources, abstain(), model, NO_EVIDENCE, degraded, rewrites
            )
        )
        return
    emitter = GroundedEmitter(len(sources))
    failed = False
    with span(SPAN_ASK_GENERATE) as current:
        raw_stream = getattr(llm, "chat_stream", None)
        if callable(raw_stream):
            stream = cast("Callable[..., AsyncIterator[object]]", raw_stream)
            try:
                async for event in stream(
                    [
                        {"role": "system", "content": ASK_SYSTEM_PROMPT},
                        {"role": "user", "content": build_prompt(question, sources)},
                    ],
                    options={"temperature": 0},
                ):
                    if not isinstance(event, dict):
                        continue
                    if event.get("error"):
                        reported = str(event.get("model", "") or "")
                        model = reported or model
                        failed = True
                        break
                    message = event.get("message")
                    piece = message.get("content", "") if isinstance(message, dict) else ""
                    if isinstance(piece, str) and piece:
                        text = emitter.feed(piece)
                        if text:
                            yield AskStreamDelta(text)
            except Exception:
                # The degrade contract: a broken seam is a note, not a raise.
                failed = True
        else:
            failed = True
        if not failed:
            text = emitter.flush()
            if text:
                yield AskStreamDelta(text)
        current.set_attribute(ATTR_MODEL, model)
    if failed or not emitter.sentences:
        note = GENERATION_UNAVAILABLE if failed else NO_CITABLE_SENTENCES
        yield AskStreamFinal(
            AnswerBundle(
                STATUS_DEGRADED, "", [], sources, abstain(), model, note, degraded, rewrites
            )
        )
        return
    answer = emitter.answer_text()
    verify = await verify_with_span(judge, answer, sources)
    yield AskStreamFinal(
        AnswerBundle(
            STATUS_SUCCESS,
            answer,
            emitter.sentences,
            sources,
            verify,
            model,
            None,
            degraded,
            rewrites,
        )
    )
