"""Wave 4 ingest validators: pure checks over the canonical document.

Three core validators live here; the ``transcript_meta`` validator lives
in ``transcript_meta.py`` beside them. The validators are pure functions
over an :class:`~corpus_kb.tools.pipeline.document.IngestDocument`; they
write nothing and never raise for a dirty document — they return findings.
The mode wrapper decides what a finding means:

* ``off``            — validators do not run (byte-identical envelope).
* ``advisory``       — the summary rides the envelope under ``validation``.
* ``enforce``        — any error-severity finding raises ``ValidationError``.
* ``calibrate``      — the summary rides the ledger ``stages`` (Wave 7 feed).
* ``transcript_meta``— the transcript metadata checks run alone; they ride
  the advisory channel and never raise.

Output is deterministic: validators run in a fixed order and findings sort
by validator, code, then chunk id.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from corpus_kb.errors import ValidationError

from .document import IngestDocument

MODES = ("off", "advisory", "enforce", "calibrate", "transcript_meta")

# Validator order is part of the output contract (determinism, zero-drift
# standard 9): callers sort findings by this rank first. Metadata checks
# are the cheapest, so ``transcript_meta`` rides first.
VALIDATOR_ORDER = ("transcript_meta", "offset_invariant", "coverage", "budget")

_WHITESPACE = frozenset(" \t\r\n")


@dataclass(frozen=True)
class Finding:
    """One validator outcome. ``detail`` never quotes document text."""

    validator: str
    code: str
    severity: str  # "error" | "warning"
    detail: str
    chunk_id: str | None = None


def resolve_mode(raw: object) -> str:
    """Validate the ``validate`` argument. Unknown values fail typed."""
    if raw is None:
        return "off"
    if raw in MODES:
        return str(raw)
    allowed = ", ".join(MODES)
    raise ValidationError(
        f"Unknown validate mode '{raw}'. Allowed values: {allowed}.",
        remediation="Set validate to off, advisory, enforce, calibrate, or transcript_meta.",
    )


def offset_invariant_findings(doc: IngestDocument) -> list[Finding]:
    """Every anchored chunk must slice back to its own text."""
    findings: list[Finding] = []
    for chunk_id in doc.offset_invariant_problems():
        findings.append(
            Finding(
                validator="offset_invariant",
                code="span-slice-mismatch",
                severity="error",
                detail=f"chunk {chunk_id} span does not slice to the chunk text",
                chunk_id=chunk_id,
            )
        )
    findings.sort(key=lambda f: (f.validator, f.code, f.chunk_id or ""))
    return findings


def coverage_findings(doc: IngestDocument) -> list[Finding]:
    """Anchored chunks must tile the document: no gaps beyond whitespace.

    Chunks without spans cannot be checked; they are skipped, not failed
    (the strategy contract keeps spans optional).
    """
    spans = [
        (chunk.source_start_char, chunk.source_end_char, str(chunk.chunk_id))
        for chunk in doc.chunks
        if chunk.source_start_char is not None and chunk.source_end_char is not None
    ]
    if not spans or not doc.text:
        return []
    spans.sort(key=lambda item: (item[0], item[1], item[2]))
    findings: list[Finding] = []
    first_start, first_end, first_id = spans[0]
    if first_start > 0 and doc.text[:first_start].strip():
        findings.append(
            Finding(
                validator="coverage",
                code="coverage-gap",
                severity="error",
                detail=f"document head before chunk {first_id} holds non-whitespace text",
                chunk_id=first_id,
            )
        )
    previous_end, previous_id = first_end, first_id
    for start, end, chunk_id in spans[1:]:
        if start < previous_end:
            findings.append(
                Finding(
                    validator="coverage",
                    code="chunk-overlap",
                    severity="error",
                    detail=f"chunk {chunk_id} overlaps chunk {previous_id} "
                    f"({previous_end - start} chars)",
                    chunk_id=chunk_id,
                )
            )
        elif start > previous_end and doc.text[previous_end:start].strip():
            findings.append(
                Finding(
                    validator="coverage",
                    code="coverage-gap",
                    severity="error",
                    detail=f"gap of {start - previous_end} chars between "
                    f"chunks {previous_id} and {chunk_id}",
                    chunk_id=chunk_id,
                )
            )
        if end >= previous_end:
            previous_end, previous_id = end, chunk_id
    if previous_end < len(doc.text) and doc.text[previous_end:].strip():
        findings.append(
            Finding(
                validator="coverage",
                code="coverage-gap",
                severity="error",
                detail=f"document tail after chunk {previous_id} holds non-whitespace text",
                chunk_id=previous_id,
            )
        )
    findings.sort(key=lambda f: (f.validator, f.code, f.chunk_id or "", f.detail))
    return findings


def budget_findings(doc: IngestDocument) -> list[Finding]:
    """Chunk sizes stay inside the sanctioned char budget (ingestion/base)."""
    from ...ingestion.base import TOKEN_BUDGET_CHARS

    findings: list[Finding] = []
    for chunk in doc.chunks:
        if len(chunk.text) > TOKEN_BUDGET_CHARS:
            findings.append(
                Finding(
                    validator="budget",
                    code="budget-exceeded",
                    severity="error",
                    detail=f"chunk {chunk.chunk_id} holds {len(chunk.text)} chars, "
                    f"over the {TOKEN_BUDGET_CHARS} char budget",
                    chunk_id=str(chunk.chunk_id),
                )
            )
    findings.sort(key=lambda f: (f.validator, f.code, f.chunk_id or ""))
    return findings


def run_validators(doc: IngestDocument) -> list[Finding]:
    """The core validators, fixed order, deterministic sort (zero-drift 9)."""
    findings = offset_invariant_findings(doc) + coverage_findings(doc) + budget_findings(doc)
    order = {name: rank for rank, name in enumerate(VALIDATOR_ORDER)}
    findings.sort(key=lambda f: (order[f.validator], f.code, f.chunk_id or "", f.detail))
    return findings


def summarize(mode: str, findings: list[Finding]) -> dict[str, Any]:
    """The stable report dict: envelope (advisory) and ledger (calibrate)."""
    validators: list[dict[str, Any]] = []
    for name in VALIDATOR_ORDER:
        owned = [f for f in findings if f.validator == name]
        validators.append(
            {
                "name": name,
                "status": "ok" if not owned else "findings",
                "findings": [
                    {
                        "code": f.code,
                        "severity": f.severity,
                        "detail": f.detail,
                        "chunk_id": f.chunk_id,
                    }
                    for f in owned
                ],
            }
        )
    return {
        "mode": mode,
        "status": "ok" if not findings else "findings",
        "problem_count": len(findings),
        "validators": validators,
    }


def validate_document(doc: IngestDocument, mode: str) -> dict[str, Any]:
    """Run the validators for one mode. Enforce fails closed (typed).

    Returns the summary dict for the advisory/calibrate callers. ``off``
    never reaches this function (the pipeline skips it entirely).
    ``transcript_meta`` runs only the metadata checks and never raises.
    """
    if mode == "transcript_meta":
        from .transcript_meta import transcript_meta_findings

        return summarize(mode, transcript_meta_findings(doc))
    findings = run_validators(doc)
    if mode == "enforce" and findings:
        names = sorted({f.validator for f in findings})
        raise ValidationError(
            f"Validation failed in enforce mode: {len(findings)} finding(s) "
            f"from {', '.join(names)}.",
            remediation="Fix the document or lower validate to advisory. Then ingest again.",
        )
    return summarize(mode, findings)
