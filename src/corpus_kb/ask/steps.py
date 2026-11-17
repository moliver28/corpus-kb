"""The ask pipeline's shared steps: retrieval, citation filter, verify.

ONE definition of the steps the batch composer (``ask/composer.py``) and
the streaming twin (``ask/streaming.py``) both run, so the two shapes
make the SAME retrieval call, apply the SAME citation rule, and produce
the SAME verification verdicts. Verification runs per claim against ONLY
the chunks the claim cites, and claims that share one citation set share
one judge call.
"""

from __future__ import annotations

from collections.abc import Callable
from difflib import SequenceMatcher
from typing import Protocol, cast
from uuid import UUID

from corpus_kb.domain.models import SearchQuery, SearchResult
from corpus_kb.handlers.query_integrity import SearchEnvelope

EntailFn = Callable[..., tuple[str, float]]
BatchEntailFn = Callable[..., list[tuple[str, float]]]

# A claim with no [n] markers may still match the answer sentence it came
# from. Below this overlap bar the match is a guess, so the claim falls
# back to the full evidence block (the honest default).
SENTENCE_MATCH_MIN_RATIO = 0.5


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
    """Decompose + entail each claim against its cited chunks only.

    The [n] markers name the chunks a claim is answerable from, so the
    entailment call sends only those texts instead of the whole evidence
    block. A claim with no markers inherits the citations of the answer
    sentence it matches best; a claim that cites nothing falls back to
    the full block. Claims that share one citation set share one judge
    call, and verdicts always come back in claim order. Mirrors the
    judge's never-raises contract: a broken judge yields an abstention,
    never a fabricated groundedness score.
    """
    if judge is None:
        return abstain()
    decompose = getattr(judge, "decompose", None)
    raw_entail = getattr(judge, "entail", None)
    raw_batch = getattr(judge, "entail_batch", None)
    entail_fn = cast("EntailFn | None", raw_entail if callable(raw_entail) else None)
    batch_fn = cast("BatchEntailFn | None", raw_batch if callable(raw_batch) else None)
    if not callable(decompose) or (entail_fn is None and batch_fn is None):
        return abstain()
    try:
        claims = decompose(answer)
    except Exception:
        return abstain()
    if not isinstance(claims, list) or not claims:
        return abstain()
    claim_texts = [str(claim) for claim in claims]
    kept = _cited_sentences(answer, len(sources))
    cited_sets = [_claim_citations(claim, kept, len(sources)) for claim in claim_texts]
    groups: dict[tuple[int, ...], list[int]] = {}
    for position, refs in enumerate(cited_sets):
        groups.setdefault(refs, []).append(position)
    verdicts: list[dict[str, object]] = [
        {"claim": claim, "label": "unsupported", "confidence": 0.0} for claim in claim_texts
    ]
    for refs, positions in groups.items():
        evidence = _evidence_slice(refs, sources)
        labels = _judge_group(batch_fn, entail_fn, [claim_texts[p] for p in positions], evidence)
        for position, (label, confidence) in zip(positions, labels, strict=False):
            verdicts[position] = {
                "claim": claim_texts[position],
                "label": label,
                "confidence": confidence,
            }
    entailed = sum(1 for verdict in verdicts if verdict["label"] == "entailed")
    return {
        "groundedness": entailed / len(verdicts),
        "verdicts": verdicts,
        "abstained": False,
    }


def _cited_sentences(answer: str, n_sources: int) -> list[dict[str, object]]:
    """The answer's cited sentences, via the ONE citation rule (the same
    ``cite_sentences`` filter that built the answer re-derives them)."""
    from corpus_kb.research.notebook import cite_sentences

    return cite_sentences(answer, n_sources)


def _claim_citations(claim: str, kept: list[dict[str, object]], n_sources: int) -> tuple[int, ...]:
    """The validated evidence indexes one claim is answerable from: its
    own [n] markers first (invalid ones ignored), else the citations of
    the sentence it matches best. Empty means "cites nothing".
    """
    from corpus_kb.research.notebook import cite_sentences

    refs: set[int] = set()
    for piece in cite_sentences(claim, n_sources):
        raw = piece.get("citations")
        if isinstance(raw, list):
            refs.update(int(index) for index in raw)
    if refs:
        return tuple(sorted(refs))
    best = _sentence_citations(claim, kept)
    return tuple(best) if best else ()


def _sentence_citations(claim: str, sentences: list[dict[str, object]]) -> list[int] | None:
    """The best-matching answer sentence's citations, or None below the
    SENTENCE_MATCH_MIN_RATIO bar (a guessed mapping would quietly widen
    or narrow a claim's evidence). Ties keep the earliest sentence."""
    best: list[int] | None = None
    best_ratio = SENTENCE_MATCH_MIN_RATIO
    for sentence in sentences:
        ratio = SequenceMatcher(None, claim.lower(), str(sentence["text"]).lower()).ratio()
        if ratio > best_ratio or (best is None and ratio >= best_ratio):
            best_ratio = max(best_ratio, ratio)
            raw = sentence.get("citations")
            best = [int(index) for index in raw] if isinstance(raw, list) else []
    return best


def _evidence_slice(refs: tuple[int, ...], sources: list[dict[str, object]]) -> list[str]:
    """The cited chunks' texts; the full block when a claim cites nothing."""
    if not refs:
        return [str(source["text"]) for source in sources]
    return [str(sources[index - 1]["text"]) for index in refs if 0 < index <= len(sources)]


def _judge_group(
    batch_fn: BatchEntailFn | None,
    entail_fn: EntailFn | None,
    claims: list[str],
    evidence: list[str],
) -> list[tuple[str, float]]:
    """One citation group's verdicts: batch when offered, per claim otherwise.

    Both paths honor the never-raises contract: a failure anywhere, or a
    batch answer with the wrong length, degrades the affected claims to
    ("unsupported", 0.0). The result is always one verdict per claim,
    in the group's claim order.
    """
    if batch_fn is not None:
        try:
            results = batch_fn(list(claims), list(evidence))
        except Exception:
            results = None
        if isinstance(results, list) and len(results) == len(claims):
            return [_normalize_pair(entry) for entry in results]
        return [("unsupported", 0.0)] * len(claims)
    if entail_fn is None:
        return [("unsupported", 0.0)] * len(claims)
    verdicts: list[tuple[str, float]] = []
    for claim in claims:
        try:
            label, confidence = entail_fn(claim, evidence)
        except Exception:
            label, confidence = "unsupported", 0.0
        verdicts.append(_normalize_pair((label, confidence)))
    return verdicts


def _normalize_pair(entry: object) -> tuple[str, float]:
    """One duck-typed verdict, hardened against junk into an honest pair."""
    if isinstance(entry, (tuple, list)) and len(entry) == 2:
        label, confidence = entry
        try:
            score = float(str(confidence))
        except (TypeError, ValueError):
            return "unsupported", 0.0
        if not 0.0 <= score <= 1.0:
            score = 0.0
        return str(label), score
    return "unsupported", 0.0


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
