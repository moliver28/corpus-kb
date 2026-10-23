"""MCP/HTTP/socket/CLI handlers for the NLI ops (AMR program).

  * :func:`nli_classify` — zero-shot labels: for each chunk, one
    entailment judgment per label hypothesis; the best score wins.
  * :func:`contradictions_screen` — judge only VECTOR-prefiltered
    candidate pairs (top-k cosine neighbors, never all pairs) and
    report the pairs the model marks CONTRADICT.

The locked prompt templates, verdict mapping, and the transport seam
live in ``analysis/nli_contract.py`` (the sensitivity pins). Labels
iterate in sorted order — a score tie resolves to the alphabetically
first label. One unreachable or unusable judgment fails the op with
an honest ``unavailable`` envelope: reason and remedy, no guessed
labels, no partial predictions. Both ops take an optional ``model``
override (None = the client default tag); on the default, the
unavailable remedy names the override keyword.
"""

from __future__ import annotations

import asyncpg
import numpy as np

from ..analysis import nli_contract as contract
from ..analysis import vectors
from ..coding.uncertainty.nli_client import NLI_MODEL
from ..errors import ValidationError
from .ingest_common import DEFAULT_TENANT_ID

__all__ = ["contradictions_screen", "nli_classify"]

_NO_CORPUS_REASON = "the tenant has no embedded chunks."
_NO_CORPUS_REMEDY = "Ingest documents with embeddings first, then retry."
_NO_TARGETS_REASON = "none of the requested chunk ids have stored vectors."
_NO_TARGETS_REMEDY = "List chunk ids that exist in this tenant, then retry."
_TOO_FEW_REASON = "fewer than two embedded chunks; no pairs to compare."
_TOO_FEW_REMEDY = "Ingest more documents, then retry."
_UNREACHABLE_REASON = "the NLI model is unreachable (transport failure)."
_UNREACHABLE_REMEDY = "Start Ollama and pull the NLI model, then retry."
_UNUSABLE_REASON = "the NLI model replied without a usable verdict word."
_UNUSABLE_REMEDY = "Check the NLI model and its Ollama version, then retry."
# Appended to the unavailable remedy when the DEFAULT model was used:
# the tag is environment-dependent, the override is one keyword away.
_DEFAULT_MODEL_HINT = ' Pass model="qwen3:4b" (or pull the qwen3 tag), then retry.'


async def _load_space(
    pg_pool: asyncpg.Pool, tenant_id: str, config: dict[str, object] | None
) -> tuple[vectors.VectorSpace, dict[str, str]]:
    """The vector+text seam (tests substitute fakes here)."""
    return await vectors.load_tenant_vectors_with_texts(pg_pool, tenant_id, config)


def _labels_checked(labels: list[str]) -> list[str]:
    """Sorted unique labels; duplicates or emptiness are typed errors."""
    if not labels:
        raise ValidationError("labels must name at least one label.")
    names = [str(label) for label in labels]
    if len(set(names)) != len(names):
        raise ValidationError("labels contains duplicate names. Remove the duplicates.")
    if any(not name for name in names):
        raise ValidationError("labels contains an empty name. Remove it.")
    return sorted(names)


def _require_int(name: str, value: int) -> int:
    """A whole number of 1 or more, else a typed 400 error."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValidationError(f"{name} must be a whole number of 1 or more.")
    return int(value)


def _unavailable(kind: str, rows_key: str, model: str) -> dict[str, object]:
    """The honest unavailable envelope (reason + remedy, zero rows).

    On the default model the remedy names the override keyword, so a
    missing tag on one machine never dead-ends the operator.
    """
    if kind == "unusable":
        reason, remedy = _UNUSABLE_REASON, _UNUSABLE_REMEDY
    else:
        reason, remedy = _UNREACHABLE_REASON, _UNREACHABLE_REMEDY
    if model == NLI_MODEL:
        remedy += _DEFAULT_MODEL_HINT
    return {
        "status": "unavailable",
        "reason": reason,
        "remedy": remedy,
        rows_key: [],
        "model": model,
        "screened_count": 0,
    }


async def nli_classify(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    *,
    labels: list[str],
    chunk_ids: list[str] | None = None,
    limit: int = 50,
    model: str | None = None,
) -> dict[str, object]:
    """Zero-shot labels via entailment; best score wins, ties go
    alphabetically.

    Returns ``{status, predictions, model, screened_count}``. Each row
    is ``{chunk_id, label, entailment_score, scores}`` with ``scores``
    listing every label by score desc then label asc. Targets are the
    requested ids (present ones, sorted) or the first ``limit`` chunk
    ids ascending; ``screened_count`` is the number of chunks judged.
    ``model`` overrides the client default tag (None keeps it); the
    envelope names the model that ran.
    """
    sorted_labels = _labels_checked(labels)
    _require_int("limit", limit)
    effective_model = model if model is not None else NLI_MODEL
    space, texts = await _load_space(pg_pool, tenant_id, config)
    if not space.ids:
        return _empty_classify(_NO_CORPUS_REASON, _NO_CORPUS_REMEDY, 0, effective_model)
    if chunk_ids is not None:
        wanted = {str(chunk_id) for chunk_id in chunk_ids}
        targets = [chunk_id for chunk_id in space.ids if chunk_id in wanted]
        if not targets:
            return _empty_classify(_NO_TARGETS_REASON, _NO_TARGETS_REMEDY, 0, effective_model)
    else:
        targets = space.ids[:limit]
    predictions: list[dict[str, object]] = []
    try:
        for target in targets:
            predictions.append(
                await _score_chunk(target, texts[target], sorted_labels, effective_model)
            )
    except contract.NliError as failure:
        return _unavailable(failure.kind, "predictions", effective_model)
    return {
        "status": "success",
        "predictions": predictions,
        "model": effective_model,
        "screened_count": len(targets),
    }


def _empty_classify(reason: str, remedy: str, screened: int, model: str) -> dict[str, object]:
    """The honest no-data envelope on the nli_classify contract keys."""
    return {
        "status": "empty",
        "reason": reason,
        "remedy": remedy,
        "predictions": [],
        "model": model,
        "screened_count": screened,
    }


async def _score_chunk(
    chunk_id: str, text: str, sorted_labels: list[str], model: str
) -> dict[str, object]:
    """Judge one chunk against every label; rank by score desc, label asc."""
    scored: list[tuple[float, str]] = []
    for label in sorted_labels:
        prompt = contract.build_classification_prompt(text, label)
        verdict = await contract.judge_verdict(prompt, model, contract.NLI_BASE_URL)
        scored.append((contract.VERDICT_SCORES[verdict], label))
    ranked = sorted(scored, key=lambda entry: (-entry[0], entry[1]))
    return {
        "chunk_id": chunk_id,
        "label": ranked[0][1],
        "entailment_score": round(ranked[0][0], 6),
        "scores": [{"label": label, "score": round(score, 6)} for score, label in ranked],
    }


async def contradictions_screen(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    candidate_k: int = 5,
    limit: int = 50,
    model: str | None = None,
) -> dict[str, object]:
    """Report vector-prefiltered pairs the model marks CONTRADICT.

    Returns ``{status, pairs, screened_count}``. Candidates are each
    chunk's top ``candidate_k`` cosine neighbors (never all pairs),
    deduped, ordered by (id_i, id_j) ascending, judged up to ``limit``.
    ``screened_count`` is the number of candidate pairs judged.
    ``model`` overrides the client default tag (None keeps it).
    """
    _require_int("candidate_k", candidate_k)
    _require_int("limit", limit)
    effective_model = model if model is not None else NLI_MODEL
    space, texts = await _load_space(pg_pool, tenant_id, config)
    if len(space.ids) < 2:
        return {
            "status": "empty",
            "reason": _TOO_FEW_REASON,
            "remedy": _TOO_FEW_REMEDY,
            "pairs": [],
            "screened_count": len(space.ids),
        }
    candidates = _candidate_pairs(space, int(candidate_k))[:limit]
    pairs: list[dict[str, object]] = []
    try:
        for id_i, id_j in candidates:
            prompt = contract.build_contradiction_prompt(texts[id_i], texts[id_j])
            verdict = await contract.judge_verdict(prompt, effective_model, contract.NLI_BASE_URL)
            if verdict == "CONTRADICT":
                pairs.append({"chunk_id_i": id_i, "chunk_id_j": id_j, "verdict": verdict})
    except contract.NliError as failure:
        return _unavailable(failure.kind, "pairs", effective_model)
    return {"status": "success", "pairs": pairs, "screened_count": len(candidates)}


def _candidate_pairs(space: vectors.VectorSpace, candidate_k: int) -> list[tuple[str, str]]:
    """Top-k cosine neighbors per chunk, deduped, (id_i, id_j) sorted.

    Neighbor picks under exact similarity ties are argpartition
    deterministic on one machine (the same-machine determinism rule).
    """
    with vectors.pin_numeric_threads():
        units = vectors.unit_rows(space.matrix)
        sims = units @ units.T
    pairs: set[tuple[str, str]] = set()
    for i, chunk_id in enumerate(space.ids):
        row = sims[i].copy()
        row[i] = -np.inf  # never pair a chunk with itself
        kth = min(candidate_k, len(row) - 1)
        picks = np.argpartition(-row, kth)[:candidate_k]
        for j in picks:
            if row[j] == -np.inf:
                continue
            first, second = sorted((chunk_id, space.ids[j]))
            pairs.add((first, second))
    return sorted(pairs)
