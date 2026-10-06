"""Coder dispatch: fetch window inputs, call the model, validate, gate quotes, route, write.

The per-agent window contract, its rendering, the per-code output type and its
validator all live in coder_window.py; the live model call lives in
coder_client.py.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any, Literal
from uuid import UUID

import asyncpg

from corpus_kb.coding.coder_client import call_ollama
from corpus_kb.coding.coder_window import (
    CODER_WINDOW_CONTRACT,
    MAX_CANDIDATE_CODES,
    CoderWindowOutput,
    build_window,
    parse_decisions,
    validate_coder_output,
)
from corpus_kb.coding.confidence_routing import Bands, route_cell
from corpus_kb.coding.qa_chaining import chain_context
from corpus_kb.coding.quote_verification import classify_quote
from corpus_kb.coding.uncertainty.tier1_logprob import logprob_entropy
from corpus_kb.coding.uncertainty.types import assemble_tier_signals
from corpus_kb.storage.tenant_conn import tenant_connection

__all__ = [
    "CODER_WINDOW_CONTRACT",
    "CoderWindowOutput",
    "dispatch_chunk",
    "validate_coder_output",
]


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


async def _fetch_qa_context(
    conn: asyncpg.Connection, tenant_id: UUID, chunk_id: UUID
) -> str | None:
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


async def dispatch_chunk(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    chunk_id: UUID,
    code_ids: list[str],
    coder: str = "test-coder",
    model: str = "qwen3:8b",
    call_fn: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
    bands: Bands | None = None,
    batch_id: str | None = None,
    coding_pass: int = 1,
) -> dict[str, Any]:
    """Dispatch a single chunk for coding against candidate codes.

    Builds the per-agent window from the chunk text and candidate code
    definitions, calls the model (injected `call_fn` under test, otherwise a live
    Ollama call with logprobs), validates the reply, runs the verbatim quote
    gate, computes Tier 0-2 signals, routes, and writes one chunk_codes row (plus
    a final_codes row) per candidate code.

    The model returns ONE decision per candidate code_id (contract item 6), so
    decision, confidence, evidence_quote and rationale are per (chunk, code).
    Tier 1 (entropy) is the exception: computed once from the logprobs of the
    single generation, hence shared by construction. Tier 2 (hedging) comes from
    each code's own rationale; Tier 0 (pooling similarity) is read from
    chunk_signals per (chunk, code), defaulting to 0.0 with a `tier0_missing`
    marker when the pair was pooled by keyword only.

    Three phases: a read transaction (window inputs), the model call outside any
    transaction, then one write transaction (quote gate, signals, routing,
    inserts). Holding a pooled connection idle-in-transaction for the full
    generation latency would exhaust the pool under batch load. All-or-nothing
    still holds: parse_decisions fails the whole call (missing or invalid
    per-code decision) before the write phase, which is itself atomic.

    Re-dispatch: chunk_codes is append-only and keyed on
    (chunk_id, code_id, coder, pass, tenant_id). Re-dispatching an already-coded
    cell inserts no audit row, so final_codes is left untouched too and a final
    decision can never reflect evidence that was never persisted. Such a cell is
    reported with `"duplicate": True` and excluded from `written`. To genuinely
    re-code a cell, pass a new `coding_pass`.

    Args:
        pool: asyncpg pool
        tenant_id: Tenant UUID
        chunk_id: Chunk to code
        code_ids: Candidate code IDs (<=5; more raises ValueError)
        coder: Coder identifier (e.g., model name or user)
        model: Model name
        call_fn: Async callable(window_text) -> response dict. Injected by tests.
        bands: Confidence bands for routing (defaults to Bands())
        batch_id: Optional batch identifier recorded on chunk_codes
        coding_pass: chunk_codes.pass value (default 1)

    Returns:
        Result dict with status and one routing row per candidate code.
    """
    if len(code_ids) > MAX_CANDIDATE_CODES:
        raise ValueError(
            f"Coder window carries at most {MAX_CANDIDATE_CODES} candidate codes, "
            f"got {len(code_ids)}"
        )
    bands = bands or Bands()

    # Phase 1 (read): fetch the window inputs and close the transaction, so the
    # pooled connection is not held (idle-in-transaction) across the model call.
    async with tenant_connection(pool, tenant_id) as conn:
        chunk_text = await conn.fetchval(
            "SELECT text FROM chunks WHERE chunk_id=$1 AND tenant_id=$2", chunk_id, tenant_id
        )
        if chunk_text is None:
            return {"status": "error", "chunk_id": chunk_id, "error": "chunk not found"}

        rows = await conn.fetch(
            """SELECT DISTINCT ON (code_id) code_id, brief_definition, inclusion_criteria,
                      exclusion_criteria, is_provisional
               FROM code_registry WHERE tenant_id=$1 AND code_id = ANY($2::text[])
               ORDER BY code_id, created_at DESC""",
            tenant_id,
            code_ids,
        )
        qa_context = await _fetch_qa_context(conn, tenant_id, chunk_id)
    code_rows = [dict(r) for r in rows]
    if not code_rows:
        return {"status": "error", "chunk_id": chunk_id, "error": "no candidate codes found"}

    # Every requested code_id must resolve. A partial resolution (e.g. a
    # stale/retired code_id) must fail loud rather than silently proceed on the
    # resolved subset while still reporting the full requested code_ids in the
    # envelope: that combination is exactly what breaks the caller invariant
    # `cells_routed == sum(len(code_ids) for cell in batch)`.
    resolved_ids = {r["code_id"] for r in code_rows}
    unresolved_ids = [cid for cid in code_ids if cid not in resolved_ids]
    if unresolved_ids:
        return {
            "status": "error",
            "chunk_id": chunk_id,
            "code_ids": code_ids,
            "error": f"unresolved code_ids: {unresolved_ids}",
            "unresolved_code_ids": unresolved_ids,
            "written": 0,
        }

    # Phase 2 (model): outside any transaction, since a generation takes seconds.
    window = build_window(chunk_text, code_rows, qa_context=qa_context)
    raw = await (call_fn(window) if call_fn is not None else call_ollama(window, model))

    decisions, error = parse_decisions(raw, [r["code_id"] for r in code_rows])
    if error:
        return {
            "status": "rejected",
            "chunk_id": chunk_id,
            "code_ids": code_ids,
            "error": error,
            "written": 0,
        }

    # One generation produced every decision, so Tier 1 entropy is shared.
    entropy = logprob_entropy(raw.get("logprobs") or [])

    # Phase 3 (write): gate, route and persist atomically. Nothing partial can be
    # written: parse_decisions already failed the whole call above if any single
    # per-code decision was missing or invalid, and this transaction is all-or-nothing.
    results = []
    async with tenant_connection(pool, tenant_id) as conn:
        for row in code_rows:
            code_id = row["code_id"]
            output = decisions[code_id]

            # Verbatim-quote gate (assign only; a reject carries no quote to verify).
            quote_status, quote_ok, stored_quote = "n/a", True, output.evidence_quote
            if output.decision == "assign":
                quote_status, repaired = classify_quote(output.evidence_quote or "", chunk_text)
                quote_ok = quote_status != "not_found"
                if quote_status == "normalized_match":
                    stored_quote = repaired

            # chunk_signals is keyed (chunk_id, code_id, signal, tenant_id) as
            # of migrations/015_chunk_signals_upsert.sql, so at most one
            # "vector" row exists per (chunk, code) here. ORDER BY created_at
            # DESC (not score DESC) is defensive: it takes the most recently
            # computed score rather than "the highest score this pair ever
            # had" if any pre-migration duplicate ever slips through.
            tier0 = await conn.fetchval(
                """SELECT score FROM chunk_signals
                   WHERE chunk_id=$1 AND code_id=$2 AND tenant_id=$3 AND signal='vector'
                   ORDER BY created_at DESC LIMIT 1""",
                chunk_id,
                code_id,
                tenant_id,
            )
            signals = assemble_tier_signals(tier0, entropy, output.rationale, quote_status)

            routed = route_cell(
                decision=_as_decision(output.decision),
                confidence=output.confidence,
                is_provisional=bool(row["is_provisional"]),
                quote_ok=quote_ok,
                bands=bands,
            )
            # chunk_codes keeps the coder's raw confidence; final_codes keeps the
            # routed one (route_cell caps provisional assigns at the mid band).
            inserted = await conn.fetchval(
                """INSERT INTO chunk_codes
                     (chunk_id, code_id, tenant_id, pass, batch_id, coder, model, decision,
                      confidence, tier_signals, rationale, evidence_quote, route, qa_context_used)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                   ON CONFLICT DO NOTHING
                   RETURNING chunk_id""",
                chunk_id,
                code_id,
                tenant_id,
                coding_pass,
                batch_id,
                coder,
                model,
                output.decision,
                output.confidence,
                signals.to_json(),
                output.rationale,
                stored_quote,
                routed.route,
                qa_context is not None,
            )
            status = _final_status(routed.route)
            # No new audit row means this (chunk, code, coder, pass) was already
            # decided; chunk_codes is append-only, so the re-dispatch is a clean
            # no-op and final_codes must NOT be updated off evidence that was
            # never persisted. The two tables can therefore never diverge.
            if inserted is not None:
                await conn.execute(
                    """INSERT INTO final_codes
                         (chunk_id, code_id, tenant_id, status, route, confidence)
                       VALUES ($1,$2,$3,$4,$5,$6)
                       ON CONFLICT (chunk_id, code_id, tenant_id) DO UPDATE
                         SET status=EXCLUDED.status, route=EXCLUDED.route,
                             confidence=EXCLUDED.confidence, decided_at=now()""",
                    chunk_id,
                    code_id,
                    tenant_id,
                    status,
                    routed.route,
                    routed.confidence,
                )
            results.append(
                {
                    "code_id": code_id,
                    "decision": output.decision,
                    "route": routed.route,
                    "status": status,
                    "confidence": output.confidence,
                    "routed_confidence": routed.confidence,
                    "evidence_quote": stored_quote,
                    "quote_status": quote_status,
                    "rationale": output.rationale,
                    "tier_signals": asdict(signals),
                    "written": inserted is not None,
                    "duplicate": inserted is None,
                    "qa_context_used": qa_context is not None,
                }
            )

    return {
        "status": "success",
        "chunk_id": chunk_id,
        "code_ids": code_ids,
        "written": sum(1 for r in results if r["written"]),
        "duplicates": sum(1 for r in results if r["duplicate"]),
        "results": results,
    }
