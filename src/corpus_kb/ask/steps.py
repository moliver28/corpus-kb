"""The ask pipeline's shared steps: retrieval, citation filter, verify.

ONE definition of the steps the batch composer (``ask/composer.py``) and
the streaming twin (``ask/streaming.py``) both run, so the two shapes
make the SAME retrieval call, apply the SAME citation rule, and produce
the SAME verification verdicts. No generation lives here — the composer
owns the generation seam; this module owns everything on both sides of
it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, cast
from uuid import UUID

from corpus_kb.domain.models import SearchQuery, SearchResult
from corpus_kb.handlers.query_integrity import SearchEnvelope


class SearchHandler(Protocol):
    """The retrieval seam: exactly what the ask pipeline needs."""

    async def handle_search_with_meta(self, query: SearchQuery) -> SearchEnvelope: ...


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


async def retrieve_evidence(
    question: str,
    handler: SearchHandler,
    tenant_id: UUID,
    k: int | None,
) -> tuple[SearchEnvelope, list[dict[str, object]], dict[str, object]]:
    """One retrieval call inside its span: envelope, evidence blocks, record.

    The honest ``degraded_excluded`` record rides along on every outcome,
    so both composer shapes answer with the same impairment receipt.
    """
    from corpus_kb.utils.tracing import ATTR_ROW_COUNT, SPAN_ASK_RETRIEVE, span

    with span(SPAN_ASK_RETRIEVE) as current:
        envelope = await handler.handle_search_with_meta(
            SearchQuery(tenant_id=tenant_id, query=question, k=k)
        )
        current.set_attribute(ATTR_ROW_COUNT, len(envelope.results))
    sources = evidence_blocks(envelope.results)
    degraded = envelope.degraded_excluded.to_dict()
    return envelope, sources, degraded


def abstain() -> dict[str, object]:
    """The honest abstention verdict set (no judge, or nothing to check)."""
    return {"groundedness": 0.0, "verdicts": [], "abstained": True}


def verify_claims(
    judge: object, answer: str, sources: list[dict[str, object]]
) -> dict[str, object]:
    """Decompose + entail per claim; abstain without a judge or claims.

    Mirrors the judge's own never-raises contract: a broken judge yields
    an abstention, never a fabricated groundedness score.
    """
    if judge is None:
        return abstain()
    decompose = getattr(judge, "decompose", None)
    entail = getattr(judge, "entail", None)
    if not callable(decompose) or not callable(entail):
        return abstain()
    entail_fn = cast("Callable[..., tuple[str, float]]", entail)
    try:
        claims = decompose(answer)
    except Exception:
        return abstain()
    if not isinstance(claims, list) or not claims:
        return abstain()
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


async def verify_with_span(
    judge: object, answer: str, sources: list[dict[str, object]]
) -> dict[str, object]:
    """Claim verification inside its span (shared by both composer shapes)."""
    from corpus_kb.utils.tracing import ATTR_CLAIMS, ATTR_GROUNDEDNESS, SPAN_ASK_VERIFY, span

    with span(SPAN_ASK_VERIFY) as current:
        verify = verify_claims(judge, answer, sources)
        verdicts = verify.get("verdicts")
        groundedness = verify.get("groundedness")
        current.set_attribute(ATTR_CLAIMS, len(verdicts) if isinstance(verdicts, list) else 0)
        current.set_attribute(
            ATTR_GROUNDEDNESS,
            float(groundedness) if isinstance(groundedness, (int, float)) else 0.0,
        )
    return verify
