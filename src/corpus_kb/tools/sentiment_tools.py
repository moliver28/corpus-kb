"""MCP/HTTP/socket/CLI handler for the sentiment screen (AMR program).

One handler per operation, shared by every surface — the
``tools/topics_tools.py`` pattern. :func:`sentiment_screen` turns the
bundled lexicon (``config/sentiment_lexicon.yaml`` and its packaged
mirror, byte-identical) into two ``lexicon`` labeling functions, scores
the tenant's chunks through the EXISTING labeling engine, and
aggregates the two signals with the existing majority-vote aggregator.

EVIDENCE CONTRACT (the do-not-claim text): this is a preset lexicon
composition, not a trained sentiment model. Two lexicon LFs cannot
support EM, so the aggregation is the honest vote fallback (the
``aggregate_signals`` floor): one-sided votes decide, ties route to
review, and a chunk with no lexicon word gets no row at all.

Honest outcomes: ``disabled`` (``analysis.sentiment.enabled`` false),
``unavailable`` (the lexicon file is missing), ``empty`` (no chunks).
A malformed lexicon file raises ``ValueError`` with the fix — a broken
input never becomes a quiet empty run. Floats round to 6 places.
"""

from __future__ import annotations

from pathlib import Path

import asyncpg
import yaml

from ..analysis.settings import load_analysis_settings
from ..coding.aggregation import aggregate_signals
from ..labeling import engine
from ..labeling.lfs import LFSpec
from ..labeling.protocol import LabelingFunction
from .ingest_common import DEFAULT_TENANT_ID, load_config_or_pass

__all__ = ["load_lexicon", "sentiment_screen"]

# Read bound (the labeling engine's window; a guard, not a target).
MAX_SCREEN_CHUNKS = 50_000

# Two preset lexicons: the aggregator's floor stays at its default 3,
# so the composition always runs the vote fallback (documented, honest).
MIN_SIGNALS = 3

_POSITIVE_LF = "lexicon-positive"
_NEGATIVE_LF = "lexicon-negative"

_DISABLED_REASON = "analysis.sentiment.enabled is false in the loaded config."
_DISABLED_REMEDY = "Set analysis.sentiment.enabled: true, then retry."

EVIDENCE_NOTE = (
    "Preset lexicon composition. This is not a trained sentiment model. "
    "Lexicon words vote; majority vote decides. Ties route to review. "
    "Check a sample by hand before you use the rows."
)

_ROUND = 6


def load_lexicon(path: str) -> tuple[list[str], list[str]]:
    """Parse the lexicon YAML; a broken file raises with the fix named."""
    try:
        raw: object = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"lexicon file {path!r} is not readable: {exc.strerror}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"lexicon file {path!r} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"lexicon file {path!r} must hold positive and negative word lists.")
    lists: list[list[str]] = []
    for name in ("positive", "negative"):
        words = raw.get(name)
        if not isinstance(words, list) or not words:
            raise ValueError(f'lexicon file {path!r}: the "{name}" list is missing or empty.')
        for word in words:
            if not isinstance(word, str) or not word.strip():
                raise ValueError(
                    f'lexicon file {path!r}: every "{name}" entry must be a non-empty string.'
                )
        lists.append([str(word) for word in words])
    positive, negative = lists
    overlap = sorted(set(positive) & set(negative))
    if overlap:
        raise ValueError(
            f"lexicon file {path!r}: the lists must stay disjoint. Shared words: "
            f"{', '.join(overlap)}."
        )
    return positive, negative


def _lexicon_spec(positive: list[str], negative: list[str]) -> LFSpec:
    """The two lexicon LFs over the sorted class vocabulary."""
    classes = ("negative", "positive")
    return LFSpec(
        classes=classes,
        lfs=(
            LabelingFunction(
                name=_NEGATIVE_LF,
                kind="lexicon",
                label="negative",
                label_index=classes.index("negative"),
                config={"terms": tuple(negative), "case_sensitive": False},
            ),
            LabelingFunction(
                name=_POSITIVE_LF,
                kind="lexicon",
                label="positive",
                label_index=classes.index("positive"),
                config={"terms": tuple(positive), "case_sensitive": False},
            ),
        ),
    )


def _honest(status: str, reason: str, remedy: str, screened: int = 0) -> dict[str, object]:
    """The honest no-op envelope on the op keys (reason + remedy named)."""
    return {
        "status": status,
        "reason": reason,
        "remedy": remedy,
        "predictions": [],
        "screened_count": screened,
    }


async def sentiment_screen(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    chunk_ids: list[str] | None = None,
    limit: int | None = None,
) -> dict[str, object]:
    """Screen the tenant's chunks with the preset lexicon (vote aggregation).

    Returns ``{status, predictions, screened_count}`` (rows: chunk_id,
    label, probability, review) plus the evidence note and a params
    record. Rows are chunk-id ascending; abstained chunks get no row.
    """
    cfg = load_config_or_pass(config)
    settings = load_analysis_settings(cfg)
    if not settings.sentiment.enabled:
        return _honest("disabled", _DISABLED_REASON, _DISABLED_REMEDY)
    lexicon_path = settings.sentiment.lexicon_path
    if not lexicon_path or not Path(lexicon_path).is_file():
        return _honest(
            "unavailable",
            f"the sentiment lexicon file was not found: {lexicon_path}.",
            "Check analysis.sentiment.lexicon_path, or reinstall the package.",
        )
    positive, negative = load_lexicon(lexicon_path)
    chunks = await engine.read_chunks(pg_pool, tenant_id, MAX_SCREEN_CHUNKS)
    if chunk_ids is not None:
        wanted = {str(chunk_id) for chunk_id in chunk_ids}
        chunks = [chunk for chunk in chunks if chunk.chunk_id in wanted]
    if not chunks:
        return _honest(
            "empty",
            "no chunks matched the selection (the tenant has no chunks, or none of the "
            "given chunk ids are stored).",
            "Ingest documents first, or pass stored chunk ids.",
        )
    spec = _lexicon_spec(positive, negative)
    matrix = engine.score_matrix(spec, chunks)
    agg = aggregate_signals(matrix, n_classes=len(spec.classes), min_signals=MIN_SIGNALS)
    rows: list[dict[str, object]] = []
    for position, chunk in enumerate(chunks):
        if bool(agg.abstain[position]):
            continue
        label_index = int(agg.labels[position])
        rows.append(
            {
                "chunk_id": chunk.chunk_id,
                "label": spec.classes[label_index],
                "probability": round(float(agg.probabilities[position, label_index]), _ROUND),
                "review": bool(agg.review[position]),
            }
        )
    rows.sort(key=lambda row: str(row["chunk_id"]))
    bounded = rows if limit is None else rows[: max(1, int(limit))]
    return {
        "status": "success",
        "predictions": bounded,
        "screened_count": len(chunks),
        "evidence_note": EVIDENCE_NOTE,
        "params": {
            "lexicon_path": lexicon_path,
            "positive_terms": len(positive),
            "negative_terms": len(negative),
            "min_signals": MIN_SIGNALS,
            "aggregation": agg.status,
            "aggregation_detail": agg.detail,
            "review_count": sum(1 for row in rows if row["review"]),
            "abstain_count": int(agg.abstain.sum()),
        },
    }
