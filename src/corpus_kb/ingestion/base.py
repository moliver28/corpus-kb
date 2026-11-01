"""Ingestion strategy contract: the ONE seam in the pipeline (plan Section 0).

Everything above storage consumes a :class:`ChunkPlan` (ordered chunk drafts +
per-chunk metadata + strategy name + detection provenance); everything below
(embed -> extract -> store) is strategy-agnostic and never changes when a new
modality is added. Built-ins (``default``, ``transcript``) and third-party
plugins (entry-point group ``corpus_kb.ingestion_strategies``) all implement
:class:`IngestionStrategy` and must pass :func:`validate_plan`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from ..utils.models import Chunk

DEFAULT_STRATEGY_NAME = "default"
TRANSCRIPT_STRATEGY_NAME = "transcript"
QA_CONTEXT_KEY = "qa_context"


class PlanConformanceError(RuntimeError):
    """A strategy's ChunkPlan violated the validate_plan invariants.

    Raised BEFORE the advisory lock and ledger: nothing has been written, so
    failing loudly is honest — a non-conformant plan (bad chunk indices,
    oversize chunks, foreign qa_context keys) would otherwise silently
    tombstone or corrupt rows downstream.
    """


# Token budget: 0.8 x 2048 tokens via the chars/4 heuristic (the plan's
# sanctioned approximation). The transcript strategy enforces it by split;
# other strategies get flagged by validate_plan.
TOKEN_BUDGET_CHARS = int(0.8 * 2048 * 4)

# How much document head (chars) strategies see at detect() time.
DETECT_HEAD_CHARS = 8192

# Detection confidence at or above which a strategy's claim is adopted
# (below it, resolution falls through to ingestion.default).
DETECT_ADOPT_CONFIDENCE = 0.5


@dataclass(frozen=True)
class DetectResult:
    """One strategy's content-detection verdict for a document head.

    ``confidence`` is 0.0..1.0; ``reason`` is a short stable token for the
    detection summary line (e.g. ``"moderator-pattern"``).
    """

    confidence: float
    reason: str


@dataclass(frozen=True)
class SourceProfile:
    """Read-only context a strategy may use to build chunks.

    ``config`` is the resolved pipeline config (optional so existing
    constructor calls keep working); a strategy that honors a config key
    (e.g. ``ingestion.tokenizer``) reads it through here, never from a
    global.
    """

    source_type: str
    is_file: bool
    path: str
    config: Mapping[str, object] | None = None


@dataclass
class PlannedChunk:
    """One chunk draft: position + text + strategy-owned metadata.

    ``metadata`` may carry the transcript strategy's ``qa_context`` key
    (no other strategy may set it — enforced by :func:`validate_plan`).
    """

    text: str
    index: int
    metadata: dict[str, object] = field(default_factory=dict)
    source_type: str = "text"
    entity_name: str | None = None
    entity_type: str | None = None
    heading_path: list[str] | None = None
    start_line: int | None = None
    end_line: int | None = None
    source_start_char: int | None = None
    source_end_char: int | None = None


@dataclass
class ChunkPlan:
    """The strategy's full output for one document.

    ``document_text`` is the canonical document text the strategy derived
    (for files, the partitioned text — exactly what the pre-registry
    pipeline used for the Document model, file hash, contextualizer, and
    shadow hierarchy). ``warnings`` carries non-fatal notes surfaced in the
    ingest result dict. ``textbook_summary`` is the textbook strategy's
    window-parse fold; it stays None for every other strategy, so the
    default ingest envelope is byte-identical.
    """

    strategy: str
    chunks: list[PlannedChunk] = field(default_factory=list)
    document_text: str = ""
    detection_confidence: float = 0.0
    detection_reason: str = ""
    empty_reason: str | None = None
    warnings: list[str] = field(default_factory=list)
    textbook_summary: dict[str, object] | None = None


class IngestionStrategy(Protocol):
    """Contract every ingestion strategy implements (built-in or plugin)."""

    name: str

    def detect(self, source_type_hint: str | None, filename: str, head: str) -> DetectResult:
        """Score how strongly the content looks like this strategy's modality."""
        ...

    def build_chunks(self, raw: str, filename: str, profile: SourceProfile) -> ChunkPlan:
        """Produce the document's ChunkPlan (replaces partition+chunk)."""
        ...


def validate_plan(plan: ChunkPlan) -> list[str]:
    """Conformance invariants for ANY registered strategy's plan (Section 0).

    Enforced by the test suite for every registered strategy, and at ingest
    time a non-empty problem list raises PlanConformanceError (before the
    advisory lock and ledger — nothing has been written), so a broken plugin
    cannot silently corrupt retrieval. Returns a list of problems (empty =
    conformant).
    """
    problems: list[str] = []
    if not plan.chunks and not plan.empty_reason:
        problems.append("plan has no chunks and no explicit empty_reason")
    indices = [chunk.index for chunk in plan.chunks]
    if indices != list(range(len(plan.chunks))):
        problems.append(f"chunk indices are not 0..n-1 continuous: {indices[:8]}")
    for chunk in plan.chunks:
        if len(chunk.text) > TOKEN_BUDGET_CHARS:
            problems.append(
                f"chunk {chunk.index} exceeds token budget "
                f"({len(chunk.text)} chars > {TOKEN_BUDGET_CHARS} = 0.8x2048 tokens)"
            )
        try:
            json.dumps(chunk.metadata)
        except (TypeError, ValueError) as exc:
            problems.append(f"chunk {chunk.index} metadata not JSON-serializable: {exc}")
        if plan.strategy != TRANSCRIPT_STRATEGY_NAME and QA_CONTEXT_KEY in chunk.metadata:
            problems.append(
                f"chunk {chunk.index} sets {QA_CONTEXT_KEY} but strategy is "
                f"'{plan.strategy}' (only '{TRANSCRIPT_STRATEGY_NAME}' may set it)"
            )
    return problems


def planned_from_chunk(chunk: Chunk, index: int) -> PlannedChunk:
    """Lift an existing pipeline Chunk into a PlannedChunk (verbatim wrap)."""
    return PlannedChunk(
        text=chunk.text,
        index=index,
        metadata=dict(chunk.metadata),
        source_type=chunk.source_type,
        entity_name=chunk.entity_name,
        entity_type=chunk.entity_type,
        heading_path=list(chunk.heading_path) if chunk.heading_path is not None else None,
        start_line=chunk.start_line,
        end_line=chunk.end_line,
        source_start_char=chunk.source_start_char,
        source_end_char=chunk.source_end_char,
    )


def chunks_from_plan(plan: ChunkPlan, document_id: str) -> list[Chunk]:
    """Materialize a plan into pipeline Chunks.

    This is the boundary: embed/extract/store see ordinary Chunks and never
    know a strategy existed.
    """
    return [
        Chunk(
            document_id=document_id,
            text=planned.text,
            source_type=planned.source_type,
            entity_name=planned.entity_name,
            entity_type=planned.entity_type,
            start_line=planned.start_line,
            end_line=planned.end_line,
            source_start_char=planned.source_start_char,
            source_end_char=planned.source_end_char,
            heading_path=list(planned.heading_path) if planned.heading_path is not None else None,
            metadata=dict(planned.metadata),
        )
        for planned in plan.chunks
    ]
