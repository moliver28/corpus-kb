"""Per-agent coder window: the numbered contract, its rendering, and the
per-code output types plus their deterministic validator.

Pure text and data transforms only. The live model call lives in
coder_client.py; dispatch (DB reads, routing, writes) lives in
coder_dispatch.py. The three-way split keeps each file under the 250-line
soft limit and keeps anything that touches a socket out of this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MAX_CANDIDATE_CODES = 5

# Numbered coder-window contract (referenced in references/coding-rules.md).
# Items 3, 4 and 5 are specified but NOT yet built: build_window renders only
# items 1, 2, 6, 7 and 8. In particular there is no truncation today, so a long
# chunk plus five code definitions can overflow the model's context silently.
CODER_WINDOW_CONTRACT = """
Per-Agent Coder Window Contract (numbered):

1. Candidate codes: <=5 per call, each with ONLY:
   - brief_definition
   - inclusion_criteria
   - exclusion_criteria
   (NO theory, NO examples beyond pooling anchor)

2. Target chunk: the text being coded

3. Chained context: Task 10 moderator→answer chain for participant turns
   (NOT YET IMPLEMENTED in Milestone 1)

4. Keyword annotations: inclusion/exclusion keyword hits for candidate codes
   (NOT YET IMPLEMENTED in Milestone 1)

5. Hard token ceiling: window is truncated if it exceeds N tokens (anchor example first)
   (NOT YET IMPLEMENTED in Milestone 1 — no truncation is applied today)

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

_OUTPUT_SHAPE = (
    '{"decisions": [{"code_id": str, "decision": "assign"|"reject", '
    '"confidence": 0.0-1.0, "evidence_quote": str|null, "rationale": str}, ...]}'
)

_WORKED_EXAMPLE = (
    'Worked example — chunk "Participant: We tested the control quarterly." with '
    "candidate codes CTRL-001 and PROC-003 gives\n"
    '{"decisions": ['
    '{"code_id": "CTRL-001", "decision": "assign", "confidence": 0.92, '
    '"evidence_quote": "We tested the control quarterly", '
    '"rationale": "Participant states the control was tested."}, '
    '{"code_id": "PROC-003", "decision": "reject", "confidence": 0.90, '
    '"evidence_quote": null, '
    '"rationale": "No documented procedure is described in this turn."}]}'
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
    """Deterministic validator for ONE per-code model decision.

    Checks:
    - decision in {'assign', 'reject'}
    - confidence in [0.0, 1.0]
    - evidence_quote present on assign

    The verbatim-quote gate (Task 6) runs separately in dispatch_chunk, since
    it needs the chunk text and produces a repaired quote, not just a verdict.

    Args:
        output: Parsed per-code coder output

    Returns:
        (is_valid, error_message)
    """
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
    """Parse the multi-decision reply into one validated output per candidate code.

    Every candidate code_id must carry exactly one decision entry; a missing,
    duplicated, unknown, or individually invalid entry fails the whole call so
    that nothing partial reaches the append-only chunk_codes table.

    `logprobs` belong to the single generation that produced all the decisions,
    so they are attached to every parsed entry (Tier 1 is shared by construction).

    Returns:
        (decisions_by_code_id, error_message). error_message is "" when valid.
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


def build_window(
    chunk_text: str,
    code_rows: list[dict[str, Any]],
    qa_context: str | None = None,
) -> str:
    """Render the per-agent coder window (contract items 1, 2, 3, 6, 7, 8).

    Carries ONLY brief_definition / inclusion_criteria / exclusion_criteria per
    candidate code: never `theory`, never the full `examples` list.

    `qa_context` is contract item 3 (chained context): the caller (dispatch)
    passes the moderator-question-chained text from
    `coding.qa_chaining.chain_context` when this chunk is a participant turn
    immediately following a moderator turn (see coder_dispatch.dispatch_chunk).
    It is rendered as its own labeled section -- `chunk_text` below it is left
    untouched -- so the verbatim-quote gate keeps checking evidence against
    the real transcript text, never the chained variant.
    """
    lines = ["You are coding one transcript turn against candidate codes.", "", "Candidate codes:"]
    for row in code_rows:
        lines.extend(
            [
                f"- {row['code_id']}",
                f"  definition: {row['brief_definition']}",
                f"  include when: {row['inclusion_criteria']}",
                f"  exclude when: {row['exclusion_criteria']}",
            ]
        )
    lines.extend(["", "Target chunk:", chunk_text])
    if qa_context:
        lines.extend(
            [
                "",
                (
                    "Chained context (the preceding moderator question, for "
                    "understanding only -- never quote from this):"
                ),
                qa_context,
            ]
        )
    lines.extend(
        [
            "",
            (
                "Output contract: reply with JSON only. Judge EVERY candidate code above "
                "independently and return exactly one entry per code_id, each with the "
                "decision key FIRST:"
            ),
            _OUTPUT_SHAPE,
            "",
            (
                "Each evidence_quote must be a verbatim substring of the target chunk, and "
                "must quote only the participant's own words, never a moderator question."
            ),
            "",
            _WORKED_EXAMPLE,
        ]
    )
    return "\n".join(lines)
