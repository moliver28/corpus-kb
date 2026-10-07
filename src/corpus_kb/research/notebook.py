"""Notebook surface — grounded Q&A leg (todo 18, v5 §15).

Project-scoped notebook question answering over the parent-child retrieval
stack (research_search). Every answer sentence carries exchange-level
citations (doc, speaker pseudonym, timestamp/turn locator); sentences the
model emits without a valid evidence index are DROPPED, never shown
uncited. ``--retrieval-only`` returns the evidence list with ZERO LLM calls
(asserted via mock in tests).

Generated answers are returned to the caller only — they are never
re-ingested as sources (v5 §15: generated summaries stay ``derived`` data).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import cast
from uuid import UUID

import asyncpg

from corpus_kb.projections.research._embed import ResearchEmbedder
from corpus_kb.research.retrieval import Citation, ResearchQuery, research_search
from corpus_kb.storage.tenant_conn import tenant_connection

CONFIDENCE_FLOORS: dict[str, tuple[str, ...]] = {
    "high": ("high",),
    "medium": ("high", "medium"),
    "low": ("high", "medium", "low"),
}
REVIEW_STATUSES = frozenset({"auto", "review", "confirmed", "overridden"})
NO_EVIDENCE_MESSAGE = "no evidence matches the requested filters"

ANSWER_SYSTEM_PROMPT = (
    "You answer qualitative-research questions STRICTLY from the numbered "
    "evidence excerpts provided. End EVERY sentence with the evidence index "
    "in square brackets, e.g. [1] or [2,3]. Never write a sentence without a "
    "citation. Never use outside knowledge. If the evidence is insufficient, "
    "say so and cite the closest excerpt."
)

_REF_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class NotebookQuery:
    """A project-scoped notebook question (tenant + project mandatory)."""

    question: str
    tenant_id: UUID
    project_id: UUID
    k: int = 6
    retrieval_only: bool = False
    source_type: str | None = None
    speaker_role: str | None = None
    topic_id: int | None = None
    doc_ids: tuple[UUID, ...] = ()
    code: str | None = None
    min_confidence: str | None = None
    review_status: str | None = None


def validate_notebook_filters(min_confidence: str | None, review_status: str | None) -> str | None:
    """Return an error message for invalid filter values, else None."""
    if min_confidence is not None and min_confidence not in CONFIDENCE_FLOORS:
        return (
            f"invalid --min-confidence {min_confidence!r}: "
            f"must be one of {', '.join(sorted(CONFIDENCE_FLOORS))}"
        )
    if review_status is not None and review_status not in REVIEW_STATUSES:
        return (
            f"invalid --review-status {review_status!r}: "
            f"must be one of {', '.join(sorted(REVIEW_STATUSES))}"
        )
    return None


async def resolve_code(pool: asyncpg.Pool, tenant_id: UUID, code: str) -> dict[str, object] | None:
    """Resolve a code NAME or UUID against the newest codebook version."""
    try:
        UUID(code)
        match_col, match_val = "cr.code_id", code
    except ValueError:
        match_col, match_val = "cr.name", code
    async with tenant_connection(pool, tenant_id) as conn:
        row = await conn.fetchrow(
            f"""
            SELECT cr.code_id, cr.name, cr.codebook_version_id AS version_id,
                   cr.brief_definition
            FROM code_registry cr
            JOIN codebook_versions v
              ON v.version_id = cr.codebook_version_id AND v.tenant_id = cr.tenant_id
            WHERE cr.tenant_id = $1 AND {match_col} = $2
            ORDER BY v.created_at DESC
            LIMIT 1
            """,
            str(tenant_id),
            match_val,
        )
    if row is None:
        return None
    return {
        "code_id": str(row["code_id"]),
        "name": str(row["name"]),
        "version_id": str(row["version_id"]),
        "definition": str(row["brief_definition"] or ""),
    }


async def notebook_ask(
    pool: asyncpg.Pool,
    embedder: ResearchEmbedder,
    llm: object,
    q: NotebookQuery,
) -> dict[str, object]:
    """Answer a notebook question with exchange-level citations."""
    filter_error = validate_notebook_filters(q.min_confidence, q.review_status)
    if filter_error is not None:
        return {"status": "error", "message": filter_error, "llm_calls": 0}

    code_id: str | None = None
    if q.code:
        resolved = await resolve_code(pool, q.tenant_id, q.code)
        if resolved is None:
            return {
                "status": "empty",
                "message": f"unknown code {q.code!r}: not in any codebook version",
                "llm_calls": 0,
            }
        code_id = str(resolved["code_id"])

    rq = ResearchQuery(
        query=q.question,
        tenant_id=q.tenant_id,
        project_id=q.project_id,
        k=q.k,
        source_type=q.source_type,
        speaker_role=q.speaker_role,
        topic_id=q.topic_id,
        doc_ids=q.doc_ids or None,
        code_id=code_id,
        min_confidence=q.min_confidence,
        review_status=q.review_status,
    )
    citations = await research_search(pool, embedder, rq)
    if not citations:
        return {"status": "empty", "message": NO_EVIDENCE_MESSAGE, "llm_calls": 0}

    evidence = await enrich_citations(pool, q.tenant_id, citations)
    if q.retrieval_only:
        return {"status": "evidence", "evidence": evidence, "llm_calls": 0}

    sentences = await generate_answer(llm, q.question, evidence)
    if not sentences:
        note = (
            "generation unavailable; returning evidence only"
            if sentences is None
            else "no citable sentences produced; returning evidence only"
        )
        return {
            "status": "evidence",
            "evidence": evidence,
            "llm_calls": 1,
            "note": note,
        }
    return {"status": "answer", "sentences": sentences, "evidence": evidence, "llm_calls": 1}


async def enrich_citations(
    pool: asyncpg.Pool, tenant_id: UUID, citations: list[Citation]
) -> list[dict[str, object]]:
    """Attach speaker pseudonyms + turn/timestamp locators to each citation."""
    doc_ids = sorted({str(c.doc_id) for c in citations})
    async with tenant_connection(pool, tenant_id) as conn:
        unit_rows = await conn.fetch(
            """
            SELECT u.doc_id, u.seq, u.t_start, s.pseudonym, s.role
            FROM research_units u
            LEFT JOIN research_speakers s ON s.speaker_id = u.speaker_id
            WHERE u.doc_id = ANY($1::uuid[])
            """,
            doc_ids,
        )
    by_doc: dict[str, dict[int, asyncpg.Record]] = {}
    for row in unit_rows:
        by_doc.setdefault(str(row["doc_id"]), {})[int(row["seq"])] = row

    out: list[dict[str, object]] = []
    for i, citation in enumerate(citations):
        units = by_doc.get(str(citation.doc_id), {})
        seqs = citation.unit_seqs
        speakers = _dedup(
            str(units[seq]["pseudonym"] or units[seq]["role"] or "speaker")
            for seq in seqs
            if seq in units
        )
        t_starts = [
            float(units[seq]["t_start"])
            for seq in seqs
            if seq in units and units[seq]["t_start"] is not None
        ]
        out.append(
            {
                "n": i + 1,
                "doc_id": str(citation.doc_id),
                "title": citation.doc_title,
                "project": citation.project_name,
                "speaker": ", ".join(speakers) if speakers else None,
                "timestamp_s": t_starts[0] if t_starts else None,
                "turn_range": [min(seqs), max(seqs)] if seqs else None,
                "exchange_seq": citation.exchange_seq,
                "question": citation.question_text,
                "answer": citation.answer_text,
                "highlighted_answer": citation.highlighted_answer(),
                "matched_kind": citation.matched_kind,
                "score": citation.score,
            }
        )
    return out


def _dedup(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _locator(evidence: dict[str, object]) -> str:
    parts = [str(evidence.get("title") or evidence.get("doc_id") or "untitled")]
    if evidence.get("speaker"):
        parts.append(str(evidence["speaker"]))
    if evidence.get("timestamp_s") is not None:
        ts = cast("float", evidence["timestamp_s"])
        parts.append(f"@ {ts:.0f}s")
    else:
        turn_range = evidence.get("turn_range")
        if isinstance(turn_range, (list, tuple)) and len(turn_range) == 2:
            parts.append(f"turns {turn_range[0]}-{turn_range[1]}")
    return " - ".join(parts)


async def generate_answer(
    llm: object, question: str, evidence: list[dict[str, object]]
) -> list[dict[str, object]] | None:
    """Grounded generation: keep ONLY sentences citing a valid evidence index."""
    raw_chat = getattr(llm, "chat", None)
    if not callable(raw_chat):
        return None
    chat = cast("Callable[..., Awaitable[object]]", raw_chat)
    blocks = []
    for item in evidence:
        text = str(item.get("question") or "") + "\n" + str(item.get("answer") or "")
        blocks.append(f"[{item['n']}] ({_locator(item)})\n{text.strip()}")
    prompt = (
        f"Question: {question}\n\nEvidence:\n" + "\n\n".join(blocks) + "\n\n"
        "Answer the question using ONLY this evidence. Every sentence ends "
        "with its evidence index like [1]."
    )
    response = await chat(
        [
            {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        options={"temperature": 0},
    )
    text = ""
    if isinstance(response, dict):
        message = response.get("message")
        if isinstance(message, dict):
            text = str(message.get("content", "") or "")
    if not text.strip():
        return None
    return cite_sentences(text, len(evidence))


def cite_sentences(answer_text: str, n_evidence: int) -> list[dict[str, object]]:
    """Split an answer into sentences and keep only cited ones.

    A sentence with NO valid evidence reference is dropped (the grounded
    contract: every answer sentence carries a citation). Invalid indices
    (out of range) invalidate the whole sentence. The [n] markers STAY in
    the sentence text (display form); ``citations`` carries the indices.
    """
    sentences: list[dict[str, object]] = []
    for raw in _SENTENCE_SPLIT_RE.split(answer_text.strip()):
        sentence = raw.strip()
        if not sentence:
            continue
        refs: list[int] = []
        valid = True
        for match in _REF_RE.finditer(sentence):
            for token in match.group(1).split(","):
                index = int(token.strip())
                if not 1 <= index <= n_evidence:
                    valid = False
                    break
                refs.append(index)
                if not valid:
                    break
        if not valid or not refs:
            continue
        sentences.append({"text": sentence, "citations": sorted(set(refs))})
    return sentences
