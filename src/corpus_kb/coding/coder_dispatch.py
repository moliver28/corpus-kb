"""Coder dispatch: fetch window inputs, call the model, validate, gate quotes, route, write.

The per-agent window contract, its rendering, the per-code output type and its
validator all live in coder_window.py; the live model call lives in
coder_client.py. The helper steps live in coder_steps.py and the cell
writer in coder_cell.py (pure moves for the 250-line rule); this module
keeps the ``dispatch_chunk`` orchestrator.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from corpus_kb.coding._scope import CodingStore, tenant_scope
from corpus_kb.coding.coder_cell import _write_cell
from corpus_kb.coding.coder_client import call_ollama
from corpus_kb.coding.coder_steps import _judgment_means, _read_window, _unresolved_error
from corpus_kb.coding.coder_window import (
    CODER_WINDOW_CONTRACT,
    MAX_CANDIDATE_CODES,
    CoderWindowOutput,
    build_window,
    parse_decisions,
    validate_coder_output,
)
from corpus_kb.coding.confidence_routing import Bands
from corpus_kb.coding.uncertainty.tier1_logprob import logprob_entropy

__all__ = [
    "CODER_WINDOW_CONTRACT",
    "CoderWindowOutput",
    "dispatch_chunk",
    "validate_coder_output",
]


async def dispatch_chunk(
    pool: CodingStore,
    tenant_id: UUID,
    chunk_id: UUID,
    code_ids: list[str],
    coder: str = "test-coder",
    model: str = "qwen3:8b",
    call_fn: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
    bands: Bands | None = None,
    batch_id: str | None = None,
    coding_pass: int = 1,
    use_judgment_mean: bool | None = None,
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

    U35 judgment distribution: ``use_judgment_mean`` (None -> read
    ``research.judgment_distribution.use_mean`` from the config) turns on
    JUDGMENT_MEAN_SAMPLES extra temperature draws of the same window and
    routes on the mean per-code confidence (``judgment_mean``), recording a
    per-row ``judgment`` summary. With the key off (the default) no extra
    call is made and the emitted rows are byte-identical to the
    single-greedy-call path.

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
        pool: asyncpg pool or SqlGateway store
        tenant_id: Tenant UUID
        chunk_id: Chunk to code
        code_ids: Candidate code IDs (<=5; more raises ValueError)
        coder: Coder identifier (e.g., model name or user)
        model: Model name
        call_fn: Async callable(window_text) -> response dict. Injected by tests;
            under the U35 mean path each invocation is read as one independent
            draw (a live call_ollama uses JUDGMENT_SAMPLE_TEMPERATURE for the
            extra draws).
        bands: Confidence bands for routing (defaults to Bands())
        batch_id: Optional batch identifier recorded on chunk_codes
        coding_pass: chunk_codes.pass value (default 1)
        use_judgment_mean: U35 override; None resolves the config key.

    Returns:
        Result dict with status and one routing row per candidate code.
    """
    if len(code_ids) > MAX_CANDIDATE_CODES:
        raise ValueError(
            f"Coder window carries at most {MAX_CANDIDATE_CODES} candidate codes, "
            f"got {len(code_ids)}"
        )
    bands = bands or Bands()

    # Phase 1 (read): the window inputs; see _read_window for the rationale.
    chunk_text, code_rows, qa_context = await _read_window(pool, tenant_id, chunk_id, code_ids)
    if chunk_text is None:
        return {"status": "error", "chunk_id": chunk_id, "error": "chunk not found"}
    if not code_rows:
        return {"status": "error", "chunk_id": chunk_id, "error": "no candidate codes found"}
    unresolved = _unresolved_error(code_ids, code_rows)
    if unresolved is not None:
        unresolved["chunk_id"] = chunk_id
        return unresolved

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
    judgment_by_code = await _judgment_means(
        window, code_ids, raw, call_fn, model, use_judgment_mean
    )

    # Phase 3 (write): gate, route and persist atomically. Nothing partial can be
    # written: parse_decisions already failed the whole call above if any single
    # per-code decision was missing or invalid, and this transaction is all-or-nothing.
    results = []
    async with tenant_scope(pool, tenant_id) as conn:
        for row in code_rows:
            results.append(
                await _write_cell(
                    conn,
                    chunk_id=chunk_id,
                    tenant_id=tenant_id,
                    coding_pass=coding_pass,
                    batch_id=batch_id,
                    coder=coder,
                    model=model,
                    row=row,
                    output=decisions[row["code_id"]],
                    entropy=entropy,
                    chunk_text=chunk_text,
                    qa_used=qa_context is not None,
                    judgment=(judgment_by_code.get(row["code_id"]) if judgment_by_code else None),
                    bands=bands,
                )
            )

    return {
        "status": "success",
        "chunk_id": chunk_id,
        "code_ids": code_ids,
        "written": sum(1 for r in results if r["written"]),
        "duplicates": sum(1 for r in results if r["duplicate"]),
        "results": results,
    }
