"""MCP/HTTP/socket/CLI handlers for the severity and emotion methods.

  * :func:`usability_severity` — register 32: rate each stored finding
    from 0 to 4 with the fixed Nielsen-style anchor rubric. Highest
    matched anchor wins; a row with no anchor match scores 0 and routes
    to review. Lexical only — no model, so the rubric runs everywhere,
    including degraded hosts.
  * :func:`emotion_tag` — register 33: one of eight fixed Plutchik
    emotions per chunk through the SAME entailment contract as
    ``nli_classify``. The taxonomy is fixed; no custom labels.

Both reuse the shared envelope shapes: severity rides the
``sentiment_screen``-style predictions envelope; emotion rides the
``nli_classify`` envelope (keys, unavailable semantics, and the
``model`` echo included) through the nli_tools envelope helpers.
Anchor matching is case-insensitive substring matching over the chunk
text; ties cannot happen because anchors are checked 4 down to 1.
"""

from __future__ import annotations

import asyncio
from typing import cast

import asyncpg

from corpus_kb.labeling import engine
from corpus_kb.labeling.engine import StoredChunk

from ..analysis import nli_contract as contract
from ..analysis import vectors
from ..coding.uncertainty.nli_client import NLI_MODEL
from ..errors import ValidationError
from .ingest_common import DEFAULT_TENANT_ID
from .nli_tools import _empty_classify, _unavailable

__all__ = ["emotion_tag", "usability_severity"]

# Read bound (a guard, not a target).
MAX_TAG_CHUNKS = 50_000

# The 0 (no anchor) default routes the row to review.
SEVERITY_NO_MATCH = 0

# Highest anchor first; the first match decides the score.
SEVERITY_ANCHORS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (
        4,
        ("crash", "data loss", "cannot continue", "loses data", "no way to", "unusable"),
    ),
    (3, ("blocked", "cannot complete", "fails", "broken", "stuck", "unable")),
    (2, ("hard to", "confusing", "struggle", "workaround", "error", "difficult", "misleading")),
    (1, ("annoying", "slow", "minor", "cosmetic", "typo", "unclear", "wording")),
)

# The fixed Plutchik set; sorted order makes score ties deterministic.
EMOTION_LABELS: tuple[str, ...] = (
    "anger",
    "anticipation",
    "disgust",
    "fear",
    "joy",
    "sadness",
    "surprise",
    "trust",
)
EMOTION_STATEMENTS: dict[str, str] = {
    label: f"The text expresses {label}." for label in EMOTION_LABELS
}

SEVERITY_NOTE = (
    "Preset anchor words from the 0-4 severity rubric. Wording is not "
    "measured impact. Check a sample of scored findings by hand."
)

_NO_CORPUS_REASON = "the tenant has no embedded chunks."
_NO_CORPUS_REMEDY = (
    "Ingest documents with embeddings first (corpus-kb research ingest), then retry."
)
_EMPTY_REASON = (
    "no chunks matched the selection (no chunks, or none of the given chunk ids are stored)."
)
_EMPTY_REMEDY = "Ingest documents first, or pass stored chunk ids."


async def _load_texts(
    pg_pool: asyncpg.Pool, tenant_id: str, config: dict[str, object] | None
) -> list[StoredChunk]:
    """The chunk-read seam (tests substitute fakes here)."""
    return await engine.read_chunks(pg_pool, tenant_id, MAX_TAG_CHUNKS)


async def _load_space(
    pg_pool: asyncpg.Pool, tenant_id: str, config: dict[str, object] | None
) -> tuple[vectors.VectorSpace, dict[str, str]]:
    """The vector+text seam for the emotion leg (tests substitute fakes)."""
    return await vectors.load_tenant_vectors_with_texts(pg_pool, tenant_id, config)


def _honest(status: str, reason: str, remedy: str, screened: int = 0) -> dict[str, object]:
    """The honest no-op envelope on the severity contract keys."""
    return {
        "status": status,
        "reason": reason,
        "remedy": remedy,
        "predictions": [],
        "screened_count": screened,
    }


def _severity_row(chunk: StoredChunk) -> dict[str, object]:
    """One rubric row: highest matched anchor wins, else 0 with review."""
    text = chunk.text.lower()
    for severity, terms in SEVERITY_ANCHORS:
        matched = [term for term in terms if term in text]
        if matched:
            return {
                "chunk_id": chunk.chunk_id,
                "severity": severity,
                "matched_terms": matched,
                "review": False,
            }
    return {
        "chunk_id": chunk.chunk_id,
        "severity": SEVERITY_NO_MATCH,
        "matched_terms": [],
        "review": True,
    }


async def usability_severity(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    chunk_ids: list[str] | None = None,
    limit: int | None = None,
) -> dict[str, object]:
    """Score the tenant's chunks with the fixed 0-4 anchor rubric.

    Returns ``{status, predictions, screened_count}`` (rows: chunk_id,
    severity, matched_terms, review) ordered severity desc then chunk id
    asc. Rows are screening flags, not measured impact.
    """
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
        raise ValidationError("limit must be a whole number of 1 or more.")
    chunks = await _load_texts(pg_pool, tenant_id, config)
    if chunk_ids is not None:
        wanted = {str(chunk_id) for chunk_id in chunk_ids}
        chunks = [chunk for chunk in chunks if chunk.chunk_id in wanted]
    if not chunks:
        return _honest("empty", _EMPTY_REASON, _EMPTY_REMEDY)
    rows = [_severity_row(chunk) for chunk in chunks]
    rows.sort(key=lambda row: (-int(cast(int, row["severity"])), str(row["chunk_id"])))
    bounded = rows if limit is None else rows[:limit]
    return {
        "status": "success",
        "predictions": bounded,
        "screened_count": len(chunks),
        "evidence_note": SEVERITY_NOTE,
        "params": {
            "anchors": [[severity, len(terms)] for severity, terms in SEVERITY_ANCHORS],
            "review_count": sum(1 for row in rows if row["review"]),
        },
    }


async def _score_emotion(chunk_id: str, text: str, model: str) -> dict[str, object]:
    """Judge one chunk against the eight statements; best score wins."""
    scored: list[tuple[float, str]] = []
    for label in EMOTION_LABELS:
        prompt = contract.build_classification_prompt(text, EMOTION_STATEMENTS[label])
        verdict = await contract.judge_verdict(prompt, model, contract.NLI_BASE_URL)
        scored.append((contract.VERDICT_SCORES[verdict], label))
    ranked = sorted(scored, key=lambda entry: (-entry[0], entry[1]))
    return {
        "chunk_id": chunk_id,
        "label": ranked[0][1],
        "entailment_score": round(ranked[0][0], 6),
        "scores": [{"label": label, "score": round(score, 6)} for score, label in ranked],
    }


async def emotion_tag(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    chunk_ids: list[str] | None = None,
    limit: int = 50,
    model: str | None = None,
) -> dict[str, object]:
    """Tag chunks with the fixed eight-emotion set via entailment.

    Returns ``{status, predictions, model, screened_count}`` (rows:
    chunk_id, label, entailment_score, scores). Targets are the
    requested ids (present ones, sorted) or the first ``limit`` chunk
    ids ascending. An unreachable or unusable model answers the honest
    ``unavailable`` envelope — no guessed labels.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValidationError("limit must be a whole number of 1 or more.")
    effective_model = model if model is not None else NLI_MODEL
    space, texts = await _load_space(pg_pool, tenant_id, config)
    if not space.ids:
        return _empty_classify(_NO_CORPUS_REASON, _NO_CORPUS_REMEDY, 0, effective_model)
    if chunk_ids is not None:
        wanted = {str(chunk_id) for chunk_id in chunk_ids}
        targets = [chunk_id for chunk_id in space.ids if chunk_id in wanted]
        if not targets:
            return _empty_classify(
                "none of the requested chunk ids have stored vectors.",
                "List chunk ids that exist in this tenant, then retry.",
                0,
                effective_model,
            )
    else:
        targets = space.ids[:limit]
    predictions: list[dict[str, object]] = []
    # Bounded concurrency: nli_client._generate builds a fresh Ollama client
    # per call in its own worker thread (no shared state), so a small gather
    # is safe. asyncio.gather keeps the request order, so the output stays
    # deterministic. No other concurrency mechanism is introduced.
    semaphore = asyncio.Semaphore(8)

    async def _score(target: str) -> dict[str, object]:
        async with semaphore:
            return await _score_emotion(target, texts[target], effective_model)

    try:
        predictions = list(await asyncio.gather(*[_score(target) for target in targets]))
    except contract.NliError as failure:
        return _unavailable(failure.kind, "predictions", effective_model)
    return {
        "status": "success",
        "predictions": predictions,
        "model": effective_model,
        "screened_count": len(targets),
    }
