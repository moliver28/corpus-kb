"""Coder dispatch helper steps (pure move out of ``coder_dispatch.py``).

The per-cell pipeline's small steps, byte-identical: route→status mapping,
the QA-chaining context fetch, decision narrowing, the phase-1 window read,
the unresolved-code guard, the U35 judgment-mean wiring, and the routed
confidence + quote gate. ``coder_dispatch.dispatch_chunk`` and
:mod:`coder_cell` compose these.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any, Literal
from uuid import UUID

from corpus_kb.coding._scope import CodingStore, ScopedConn, tenant_scope
from corpus_kb.coding.coder_window import CoderWindowOutput
from corpus_kb.coding.judgment_sampling import (
    JudgmentMeanResult,
    judgment_mean_enabled,
    sample_judgments,
)
from corpus_kb.coding.qa_chaining import chain_context
from corpus_kb.coding.quote_verification import classify_quote

# Every route_cell output must have an explicit entry here (fail loud on an
# unmapped route rather than silently defaulting it into manual_review).
_ROUTE_STATUS = {
    "high-conf-accept": "accepted",
    "high-conf-reject": "rejected",
    "manual_review": "manual_review",
    "provisional-hold": "manual_review",
    "mid-band": "manual_review",
    "escalate": "manual_review",
    "low-conf-escalate": "manual_review",
}


def _final_status(route: str) -> str:
    """Map a route to a final_codes.status (accepted|rejected|manual_review)."""
    try:
        return _ROUTE_STATUS[route]
    except KeyError:
        raise ValueError(f"Unmapped route for final_codes.status: {route!r}") from None


async def _fetch_qa_context(conn: ScopedConn, tenant_id: UUID, chunk_id: UUID) -> str | None:
    """The moderator-chained context for this chunk, or None when inapplicable.

    Inert unless: (a) this chunk has a speaker_role in chunk_status (written
    by speaker_role_projection.classify_speaker_roles off chunk metadata --
    corpora with no speaker metadata never get one), (b) that role is NOT
    "moderator" itself, and (c) the immediately preceding chunk in the same
    document IS classified "moderator". When all three hold, this is a
    participant turn answering a moderator's question (contract item 3), and
    `qa_chaining.chain_context` produces the chained text.
    """
    row = await conn.fetchrow(
        """SELECT c.doc_id, c.chunk_index, cs.speaker_role
           FROM chunks c LEFT JOIN chunk_status cs
             ON cs.chunk_id = c.chunk_id AND cs.tenant_id = c.tenant_id
           WHERE c.chunk_id=$1 AND c.tenant_id=$2""",
        chunk_id,
        tenant_id,
    )
    if row is None or not row["speaker_role"] or row["speaker_role"] == "moderator":
        return None

    prior = await conn.fetchrow(
        """SELECT c.text, cs.speaker_role
           FROM chunks c LEFT JOIN chunk_status cs
             ON cs.chunk_id = c.chunk_id AND cs.tenant_id = c.tenant_id
           WHERE c.tenant_id=$1 AND c.doc_id=$2 AND c.chunk_index=$3""",
        tenant_id,
        row["doc_id"],
        row["chunk_index"] - 1,
    )
    if prior is None or prior["speaker_role"] != "moderator":
        return None

    chunk_text = await conn.fetchval(
        "SELECT text FROM chunks WHERE chunk_id=$1 AND tenant_id=$2", chunk_id, tenant_id
    )
    return chain_context(role="participant", moderator_q=prior["text"], answer=chunk_text or "")


def _as_decision(decision: str) -> Literal["assign", "reject"]:
    """Narrow a validated decision string to the literal route_cell accepts.

    parse_decisions has already rejected anything outside the pair, so the
    ValueError is unreachable in practice; it exists so the narrowing is proven
    to the type checker rather than asserted by a cast.
    """
    if decision == "assign":
        return "assign"
    if decision == "reject":
        return "reject"
    raise ValueError(f"Invalid decision reached routing: {decision!r}")


async def _read_window(
    pool: CodingStore, tenant_id: UUID, chunk_id: UUID, code_ids: list[str]
) -> tuple[str | None, list[dict[str, Any]], str | None]:
    """Phase 1 (read): fetch the window inputs and close the transaction, so
    the pooled connection is not held (idle-in-transaction) across the model
    call. Returns (chunk_text, code_rows, qa_context); chunk_text is None when
    the chunk does not exist.
    """
    async with tenant_scope(pool, tenant_id) as conn:
        chunk_text = await conn.fetchval(
            "SELECT text FROM chunks WHERE chunk_id=$1 AND tenant_id=$2", chunk_id, tenant_id
        )
        if chunk_text is None:
            return None, [], None

        rows = await conn.fetch(
            """SELECT DISTINCT ON (code_id) code_id, brief_definition, inclusion_criteria,
                      exclusion_criteria, is_provisional
               FROM code_registry WHERE tenant_id=$1 AND code_id = ANY($2::text[])
               ORDER BY code_id, created_at DESC""",
            tenant_id,
            code_ids,
        )
        qa_context = await _fetch_qa_context(conn, tenant_id, chunk_id)
    return chunk_text, [dict(r) for r in rows], qa_context


def _unresolved_error(
    code_ids: list[str], code_rows: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Error envelope for a partial code resolution, or None when all resolved.

    Every requested code_id must resolve. A partial resolution (e.g. a
    stale/retired code_id) must fail loud rather than silently proceed on the
    resolved subset while still reporting the full requested code_ids in the
    envelope: that combination is exactly what breaks the caller invariant
    `cells_routed == sum(len(code_ids) for cell in batch)`.
    """
    resolved_ids = {r["code_id"] for r in code_rows}
    unresolved_ids = [cid for cid in code_ids if cid not in resolved_ids]
    if not unresolved_ids:
        return None
    return {
        "status": "error",
        "chunk_id": None,  # patched by the caller, which owns the chunk id
        "code_ids": code_ids,
        "error": f"unresolved code_ids: {unresolved_ids}",
        "unresolved_code_ids": unresolved_ids,
        "written": 0,
    }


async def _judgment_means(
    window: str,
    code_ids: list[str],
    raw: dict[str, Any],
    call_fn: Callable[[str], Awaitable[dict[str, Any]]] | None,
    model: str,
    use_judgment_mean: bool | None,
) -> dict[str, JudgmentMeanResult] | None:
    """U35: resolve the mean-path flag and sample when it is on.

    Explicit argument wins; None resolves
    research.judgment_distribution.use_mean from the config (default off ->
    no sampling at all). The sampling mechanics (extra temperature draws,
    parse-skip honesty) live in coding/judgment_sampling.py; this is the
    wiring only.
    """
    if use_judgment_mean is None:
        use_judgment_mean = judgment_mean_enabled()
    if not use_judgment_mean:
        return None
    return await sample_judgments(window, code_ids, raw, call_fn, model)


def _routed_confidence(
    output: CoderWindowOutput, judgment: JudgmentMeanResult | None
) -> tuple[float, dict[str, object] | None]:
    """U35: the routed confidence is the judgment-distribution mean when the
    mean path ran and produced one; otherwise the greedy confidence,
    unchanged. Also returns the per-row judgment summary block (None when the
    mean path did not run — with use_mean off the rows are byte-identical to
    before).
    """
    effective_confidence = output.confidence
    judgment_block: dict[str, object] | None = None
    if judgment is not None:
        if judgment.used_mean and judgment.mean_value is not None:
            effective_confidence = judgment.mean_value
        judgment_block = asdict(judgment)
    return effective_confidence, judgment_block


def _quote_gate(output: CoderWindowOutput, chunk_text: str) -> tuple[str, bool, str | None]:
    """Verbatim-quote gate (assign only; a reject carries no quote to verify).

    Returns (quote_status, quote_ok, stored_quote); a normalized match stores
    the repaired quote.
    """
    quote_status, quote_ok, stored_quote = "n/a", True, output.evidence_quote
    if output.decision == "assign":
        quote_status, repaired = classify_quote(output.evidence_quote or "", chunk_text)
        quote_ok = quote_status != "not_found"
        if quote_status == "normalized_match":
            stored_quote = repaired
    return quote_status, quote_ok, stored_quote
