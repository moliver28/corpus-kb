"""Degraded-policy + search-honesty primitives for the query handler.

Stage 3 of the ingestion-rigor program: degraded candidate filtering, the
all-zero query-embedding guard, and the honest ``degraded_excluded``
envelope. Lives here (not in query_handler.py, already past the 250-line
soft limit) so both the handler and the API adapters share one definition.

Contracts (plan §Stage 3):
  * candidate CTEs exclude ``chunks_vectors.degraded`` rows when
    ``search.exclude_degraded`` is true (default true); SEED subqueries are
    never filtered — seed documents stay discoverable when degraded.
  * an all-zero query embedding abstains the vector arm (reason
    ``zero-query-embedding``); the lexical arm still runs. This makes the
    NaN ordering hazard (0/0 cosine sorting first under DESC) unreachable.
  * every search envelope carries ``degraded_excluded`` {count, reasons};
    when all vector candidates were excluded (results empty + recorded
    reasons) the status is ``degraded_empty`` with the pinned remedy.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from corpus_kb.domain.models import SearchResult
from corpus_kb.utils.tracing import current_trace_id

# Pinned remedy (plan §Stage 3.3): shown whenever a search returns no
# results under degraded conditions. ``corpus-kb ingest --reembed`` is the
# Stage-5 CLI surface for the reembed helper (tools/ingest_common.py).
REEMBED_REMEDY = (
    "Search returned no results under degraded conditions "
    "(embedding unavailable or all candidates flagged degraded). "
    "Start Ollama, then run: corpus-kb ingest --reembed"
)

# Vector-arm abstention reasons recorded in ``degraded_excluded.reasons``.
REASON_ZERO_QUERY = "zero-query-embedding"
REASON_VECTOR_ARM_ERROR = "vector-arm-error"
REASON_NO_EMBEDDER = "no-embedder"
REASON_FILTER_EMPTY = "degraded-filter-active"
# W3: the score-based reranker graded pairs generatively (binary yes/no
# parses, not cross-encoder scores). Rides the existing reasons list so the
# search envelope key-set stays pinned.
REASON_RERANK_MODE_GENERATIVE = "rerank-mode-generative-binary"

STATUS_SUCCESS = "success"
STATUS_DEGRADED_EMPTY = "degraded_empty"


@dataclass(frozen=True)
class DegradedExcluded:
    """Honest record of what the search could NOT see, and why.

    ``count`` is the number of candidate rows the degraded filter excluded
    (from the handler's count probe; 0 when no probe ran — i.e. no rows
    were excluded by the filter). ``reasons`` are the recorded arm
    impairments (zero-query embedding, vector-arm error, no embedder,
    filter-active) — an impairment can be present with count 0.
    """

    count: int = 0
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {"count": self.count, "reasons": list(self.reasons)}


@dataclass(frozen=True)
class SearchEnvelope:
    """Search results plus the degraded-honesty metadata.

    ``status`` is ``success`` or ``degraded_empty`` (results empty AND at
    least one arm-impairment reason recorded). ``remedy`` carries the pinned
    reembed message only in the ``degraded_empty`` case. ``trace_id`` is
    captured inside the ``corpus_kb.query`` span (finalize_envelope) — it is
    None whenever tracing is off, and ``envelope_to_payload`` only emits the
    key when it is set, so the off-path envelope shape is unchanged.
    """

    status: str
    results: list[SearchResult]
    degraded_excluded: DegradedExcluded
    remedy: str | None = None
    trace_id: str | None = None


def is_all_zero(vector: list[float] | None) -> bool:
    """True when the query embedding is the degraded-mode all-zero marker."""
    return vector is not None and all(v == 0.0 for v in vector)


def degraded_candidate_predicate(enabled: bool, alias: str = "cv") -> str:
    """SQL fragment excluding degraded vector rows from candidate CTEs.

    Empty string when disabled, so flag-off SQL is semantically identical to
    the pre-stage shape (trailing whitespace only). NEVER apply this to seed
    subqueries — the seed vector lookup must find the chunk even when that
    chunk is flagged degraded.
    """
    if not enabled:
        return ""
    return f"AND {alias}.degraded = FALSE"


def finalize_envelope(
    results: list[SearchResult],
    reasons: list[str],
    excluded_count: int = 0,
) -> SearchEnvelope:
    """Build the final envelope: degraded_empty iff empty results + reasons.

    ``excluded_count`` is the candidate-row count the degraded filter
    removed (from the handler's probe; 0 when the probe never ran). It
    becomes ``DegradedExcluded.count`` — reasons and count are independent:
    an arm impairment (zero-query, arm error) can be recorded with count 0.

    The active trace id (if any) is captured HERE — finalize runs inside the
    ``corpus_kb.query`` span, so the id is the one shared by the whole span
    tree, and every surface that later serializes the envelope carries it.
    """
    trace_id = current_trace_id()
    reasons_out = list(reasons)
    if not results and reasons_out:
        return SearchEnvelope(
            status=STATUS_DEGRADED_EMPTY,
            results=[],
            degraded_excluded=DegradedExcluded(count=excluded_count, reasons=reasons_out),
            remedy=REEMBED_REMEDY,
            trace_id=trace_id,
        )
    return SearchEnvelope(
        status=STATUS_SUCCESS,
        results=results,
        degraded_excluded=DegradedExcluded(count=excluded_count, reasons=reasons_out),
        trace_id=trace_id,
    )


def envelope_to_payload(envelope: SearchEnvelope) -> dict[str, object]:
    """Serialize the envelope for the HTTP/socket response.

    Additive over the legacy {status, result} shape: existing consumers
    reading status+result keep working; honest clients read the new field.
    ``trace_id`` appears ONLY when the envelope captured a live trace
    (tracing enabled) — with tracing off the key-set is byte-identical to
    the pre-tracing shape.
    """
    payload: dict[str, object] = {
        "status": envelope.status,
        "result": [r.model_dump(mode="json") for r in envelope.results],
        "degraded_excluded": envelope.degraded_excluded.to_dict(),
    }
    if envelope.remedy is not None:
        payload["remedy"] = envelope.remedy
    if envelope.trace_id is not None:
        payload["trace_id"] = envelope.trace_id
    return payload
