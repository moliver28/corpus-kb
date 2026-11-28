"""Per-agent coder window: the numbered contract, its rendering, and the
per-code output types plus their deterministic validator.

Pure text and data transforms only. The live model call lives in
coder_client.py; dispatch (DB reads, routing, writes) lives in
coder_dispatch.py. TAO T1 (TD-03): the window body renders through the
prompt registry (``coder_cell``; the inline tail moved verbatim into the YAML, D-8).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from corpus_kb.chunking.hierarchy import estimate_tokens
from corpus_kb.coding.prompt_registry import RenderedPrompt, render_prompt
from corpus_kb.coding.window_ceiling import (
    DEFAULT_MAX_WINDOW_TOKENS,
    fit_to_tokens,
    reserve_budget,
)

MAX_CANDIDATE_CODES = 5

# Numbered coder-window contract (referenced in references/coding-rules.md).
# build_window renders items 1, 2, 3, 5, 6, 7 and 8. Item 4 (keyword
# annotations) stays specified-but-not-built until an upstream keyword
# source exists; item 5's ceiling lives in coding/window_ceiling.py.
CODER_WINDOW_CONTRACT = """
Per-Agent Coder Window Contract (numbered):

1. Candidate codes: <=5 per call, each with ONLY:
   - brief_definition
   - inclusion_criteria
   - exclusion_criteria
   (NO theory, NO examples beyond pooling anchor)

2. Target chunk: the text being coded

3. Chained context: Task 10 moderator→answer chain for participant turns
   (BUILT: render the qa_context argument in build_window)

4. Keyword annotations: inclusion/exclusion keyword hits for candidate codes
   (NOT YET IMPLEMENTED — no upstream keyword source exists)

5. Hard token ceiling: window is truncated if it exceeds N tokens (anchor example first)
   (BUILT: coding/window_ceiling.py; the anchor example is never cut)

6. Output contract (JSON only): ONE decision per candidate code_id, never one
   blended verdict for the batch:
   {"decisions": [
      {"code_id": str, "decision": "assign"|"reject", "confidence": 0.0-1.0,
       "evidence_quote": str|null, "rationale": str}, ...]}
   Every candidate code shown must appear exactly once. The decision token is
   emitted first within each entry for Tier 1 entropy measurement.

7. Anti-priming instruction: cite only participant's own words, never moderator question

8. One worked example provided
"""

# Shared by the frame measurement and the assembly, so the computed token
# ceiling covers exactly the rendered structure.
_CONTEXT_LABEL = (
    "Chained context (the preceding moderator question, for "
    "understanding only -- never quote from this):"
)


def _candidate_block(code_rows: list[dict[str, Any]]) -> str:
    """The candidate-code lines: brief definition + criteria, never theory."""
    lines: list[str] = []
    for row in code_rows:
        lines.extend(
            [
                f"- {row['code_id']}",
                f"  definition: {row['brief_definition']}",
                f"  include when: {row['inclusion_criteria']}",
                f"  exclude when: {row['exclusion_criteria']}",
            ]
        )
    return "\n".join(lines)


def chained_variable(qa_context: str | None, text: str = "") -> str:
    """Labeled slot (D-8); empty text reserves the label's frame tokens."""
    if qa_context is None:
        return ""
    return f"\n\n{_CONTEXT_LABEL}\n{text}"


def build_window_prompt(
    code_rows: list[dict[str, Any]], chunk_text: str, chained_context: str
) -> RenderedPrompt:
    """Render the ``coder_cell`` prompt from the registry (TD-03)."""
    return render_prompt(
        "coder_cell",
        {
            "candidate_codes": _candidate_block(code_rows),
            "target_chunk": chunk_text,
            "chained_context": chained_context,
        },
    )


@dataclass
class CoderWindowOutput:
    """One parsed per-code decision from the coder model.

    `code_id` identifies which candidate code this decision judges. It is
    optional only so the dataclass stays constructible in validator unit tests;
    dispatch always populates it from the model's own entry.
    """

    decision: str
    confidence: float
    evidence_quote: str | None
    rationale: str
    logprobs: list[dict[str, Any]] | None = None
    code_id: str | None = None


def validate_coder_output(output: CoderWindowOutput) -> tuple[bool, str]:
    """Validate ONE decision; the verbatim-quote gate runs in dispatch_chunk."""
    if output.decision not in ("assign", "reject"):
        return False, f"Invalid decision: {output.decision}"

    if not (0.0 <= output.confidence <= 1.0):
        return False, f"Confidence out of range: {output.confidence}"

    if output.decision == "assign" and not output.evidence_quote:
        return False, "Evidence quote required for assign decision"

    return True, ""


def parse_decisions(
    raw: dict[str, Any], code_ids: list[str]
) -> tuple[dict[str, CoderWindowOutput], str]:
    """Parse the reply into one validated output per candidate code.

    Every candidate code_id carries exactly one decision entry; a missing,
    duplicated, unknown, or invalid entry fails the whole call, so nothing
    partial reaches the append-only chunk_codes table. `logprobs` belong
    to the one generation, so they attach to every entry (Tier 1 shared).

    Returns (decisions_by_code_id, error_message); "" means valid.
    """
    if raw.get("error"):
        return {}, f"Model call failed: {raw['error']}"

    entries = raw.get("decisions")
    if not isinstance(entries, list) or not entries:
        return {}, "Model output missing a non-empty 'decisions' list"

    logprobs = raw.get("logprobs")
    parsed: dict[str, CoderWindowOutput] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            return {}, f"Decision entry is not an object: {entry!r}"
        code_id = str(entry.get("code_id", ""))
        if code_id not in code_ids:
            return {}, f"Decision for unknown code_id: {code_id!r}"
        if code_id in parsed:
            return {}, f"Duplicate decision for code_id: {code_id}"
        try:
            confidence = float(entry.get("confidence", -1.0))
        except (TypeError, ValueError):
            confidence = -1.0  # non-numeric confidence fails the range check below
        output = CoderWindowOutput(
            decision=str(entry.get("decision")),
            confidence=confidence,
            evidence_quote=entry.get("evidence_quote"),
            rationale=str(entry.get("rationale", "")),
            logprobs=logprobs,
            code_id=code_id,
        )
        is_valid, error = validate_coder_output(output)
        if not is_valid:
            return {}, f"{code_id}: {error}"
        parsed[code_id] = output

    missing = [c for c in code_ids if c not in parsed]
    if missing:
        return {}, f"Missing decisions for candidate codes: {', '.join(missing)}"
    return parsed, ""


def build_window_prompt_fitted(
    code_rows: list[dict[str, Any]],
    chunk_text: str,
    qa_context: str | None = None,
    max_tokens: int | None = None,
) -> RenderedPrompt:
    """Fit the window to the hard ceiling, then render via the registry.

    The contract item 5 rules: the anchor is never cut, the context yields
    before the chunk, cuts carry "[truncated]", and a ceiling that cannot
    hold the chunk raises ValueError. The digest covers the FITTED variable
    values, so it binds the body actually sent (momus P1, T1 fix round) —
    dispatch renders through this, never through the raw pieces.
    """
    ceiling = DEFAULT_MAX_WINDOW_TOKENS if max_tokens is None else max_tokens
    frame = build_window_prompt(code_rows, "", chained_variable(qa_context)).rendered
    fixed = estimate_tokens(frame)
    budget = reserve_budget(ceiling, fixed)
    chunk_fit = fit_to_tokens(chunk_text, budget)
    if chunk_text and not chunk_fit:
        raise ValueError(
            f"Coder window ceiling {ceiling} leaves no room for the target chunk. Raise max_tokens."
        )
    context_fit = ""
    if qa_context:
        context_fit = fit_to_tokens(qa_context, budget - estimate_tokens(chunk_fit))
    # No fitted context means the label yields too (the pre-registry rule).
    chained = "" if not context_fit else chained_variable(qa_context, context_fit)
    return build_window_prompt(code_rows, chunk_fit, chained)


def build_window(
    chunk_text: str,
    code_rows: list[dict[str, Any]],
    qa_context: str | None = None,
    max_tokens: int | None = None,
) -> str:
    """Render the per-agent coder window (contract items 1, 2, 3, 5, 6, 7, 8).

    Candidate codes carry ONLY definition/criteria fields. `qa_context` is
    its own labeled section, so the quote gate checks the real transcript.
    This is the fitted registry render flattened to its text; the ceiling
    rules live on ``build_window_prompt_fitted``.
    """
    return build_window_prompt_fitted(
        code_rows, chunk_text, qa_context, max_tokens
    ).rendered.rstrip("\n")
