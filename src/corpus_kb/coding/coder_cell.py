"""The coder dispatch cell writer (pure move out of ``coder_dispatch.py``).

``_write_cell`` is the byte-identical body: gate, route and persist ONE
(chunk, code) cell; ``coder_dispatch.dispatch_chunk`` calls it inside the
phase-3 write transaction.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any
from uuid import UUID

from corpus_kb.coding._scope import ScopedConn
from corpus_kb.coding.coder_steps import (
    _as_decision,
    _final_status,
    _quote_gate,
    _routed_confidence,
)
from corpus_kb.coding.coder_window import CoderWindowOutput
from corpus_kb.coding.confidence_routing import Bands, RouteResult, route_cell
from corpus_kb.coding.grade import (
    TRANSLATION_UNTRANSLATED,
    GradeSignals,
    grade_cell,
    reflexive_route,
)
from corpus_kb.coding.judgment_sampling import JudgmentMeanResult
from corpus_kb.coding.uncertainty.types import assemble_tier_signals


async def _write_cell(
    conn: ScopedConn,
    *,
    chunk_id: UUID,
    tenant_id: UUID,
    coding_pass: int,
    batch_id: str | None,
    coder: str,
    model: str,
    row: dict[str, Any],
    output: CoderWindowOutput,
    entropy: float | None,
    chunk_text: str,
    qa_used: bool,
    judgment: JudgmentMeanResult | None,
    bands: Bands,
    paradigm: str | None = None,
    prompt_id: str,
    prompt_version: str,
    prompt_digest: str,
) -> dict[str, Any]:
    """Gate, route and persist ONE (chunk, code) cell; return its result row.

    chunk_signals is keyed (chunk_id, code_id, signal, tenant_id) as
    of migrations/015_chunk_signals_upsert.sql, so at most one
    "vector" row exists per (chunk, code) here. ORDER BY created_at
    DESC (not score DESC) is defensive: it takes the most recently
    computed score rather than "the highest score this pair ever
    had" if any pre-migration duplicate ever slips through.
    """
    code_id = row["code_id"]
    effective_confidence, judgment_block = _routed_confidence(output, judgment)
    quote_status, quote_ok, stored_quote = _quote_gate(output, chunk_text)

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
        confidence=effective_confidence,
        is_provisional=bool(row["is_provisional"]),
        quote_ok=quote_ok,
        bands=bands,
    )
    # T1 grade (TD-07): honest U until the T2 pilot feeds a calibration split.
    graded = grade_cell(
        GradeSignals(
            decision=output.decision,
            paradigm=paradigm,
            quote_ok=quote_ok,
            translation_state=TRANSLATION_UNTRANSLATED,
        )
    )
    if paradigm == "reflexive":
        # TD-08: no route ends in an accept without a human decision.
        routed = RouteResult(route=reflexive_route(routed.route), confidence=routed.confidence)
    # chunk_codes keeps the routed-effective confidence (the U35 mean
    # when the mean path ran, else the coder's raw confidence);
    # final_codes keeps the routed one (route_cell caps provisional
    # assigns at the mid band).
    inserted = await conn.fetchval(
        """INSERT INTO chunk_codes
             (chunk_id, code_id, tenant_id, pass, batch_id, coder, model, decision,
              confidence, tier_signals, rationale, evidence_quote, route, qa_context_used,
              grade, grade_reason, prompt_id, prompt_version, prompt_digest)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)
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
        effective_confidence,
        signals.to_json(),
        output.rationale,
        stored_quote,
        routed.route,
        qa_used,
        graded.grade,
        graded.reason,
        # TD-03 point 4 (AM-12): durable prompt provenance on the coded row.
        prompt_id,
        prompt_version,
        prompt_digest,
    )
    status = _final_status(routed.route)
    # No new audit row means this (chunk, code, coder, pass) was already
    # decided; chunk_codes is append-only, so the re-dispatch is a clean
    # no-op and final_codes must NOT be updated off evidence that was
    # never persisted. The two tables can therefore never diverge.
    if inserted is not None:
        await conn.execute(
            """INSERT INTO final_codes
                 (chunk_id, code_id, tenant_id, status, route, confidence, grade, grade_reason)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
               ON CONFLICT (chunk_id, code_id, tenant_id) DO UPDATE
                 SET status=EXCLUDED.status, route=EXCLUDED.route,
                     confidence=EXCLUDED.confidence, decided_at=now(),
                     grade=EXCLUDED.grade, grade_reason=EXCLUDED.grade_reason""",
            chunk_id,
            code_id,
            tenant_id,
            status,
            routed.route,
            routed.confidence,
            graded.grade,
            graded.reason,
        )
    result_row: dict[str, Any] = {
        "code_id": code_id,
        "decision": output.decision,
        "route": routed.route,
        "status": status,
        "confidence": effective_confidence,
        "routed_confidence": routed.confidence,
        "evidence_quote": stored_quote,
        "quote_status": quote_status,
        "rationale": output.rationale,
        "tier_signals": asdict(signals),
        "grade": graded.grade,
        "grade_reason": graded.reason,
        "prompt_id": prompt_id,
        "prompt_version": prompt_version,
        "prompt_digest": prompt_digest,
        "written": inserted is not None,
        "duplicate": inserted is None,
        "qa_context_used": qa_used,
    }
    if judgment_block is not None:
        result_row["judgment"] = judgment_block
    return result_row
