"""Codebook release gate (U7 definition completeness; v6 section 7).

Pure functions over ``(GateInputs, ResolvedProfile)`` — no queries here, the
reads live in ``gate_reads.py``. Honesty rule: a gate never upgrades missing
evidence into a pass; ``not_evaluable`` / ``insufficient_data`` /
``invalid_output`` travel in status+reason.

U7 definition_completeness — every code carries a definition, inclusion and
exclusion criteria, at least one POSITIVE and one NEAR-MISS exemplar, and a
valid parent reference when the codebook uses hierarchy. Near-miss polarity
is read from an existing exemplar entry's ``polarity``/``kind`` field
(``"near_miss"``); plain string exemplars count as positive.
"""

from __future__ import annotations

from corpus_kb.research.release.gate_support import as_dict, as_list, as_str
from corpus_kb.research.release.gate_types import (
    GATE_DEFINITION_COMPLETENESS,
    GateInputs,
    GateResult,
)
from corpus_kb.research.release.profiles import ResolvedProfile


def _parent_of(raw: dict[str, object]) -> str | None:
    """A code's parent reference: ``parent_id`` key or theory block."""
    return as_str(raw.get("parent_id")) or as_str(as_dict(raw.get("theory")).get("parent_id"))


def _exemplar_polarity(examples: object) -> tuple[int, int]:
    """(positive, near_miss) counts over the ``examples`` entries."""
    positive = 0
    near_miss = 0
    for entry in as_list(examples):
        if isinstance(entry, dict):
            polarity = as_str(entry.get("polarity")) or as_str(entry.get("kind")) or "positive"
            if polarity in ("near_miss", "negative"):
                near_miss += 1
            else:
                positive += 1
        elif isinstance(entry, str) and entry.strip():
            positive += 1
    return positive, near_miss


def _code_defects(raw: dict[str, object], parent_valid: bool | None) -> list[str]:
    """U7 defects for ONE code (definition/criteria/exemplars/parent)."""
    defects: list[str] = []
    if as_str(raw.get("brief_definition")) is None:
        defects.append("missing definition")
    if as_str(raw.get("inclusion_criteria")) is None:
        defects.append("missing inclusion_criteria")
    if as_str(raw.get("exclusion_criteria")) is None:
        defects.append("missing exclusion_criteria")
    positive, near_miss = _exemplar_polarity(raw.get("examples"))
    if positive < 1:
        defects.append("missing positive exemplar")
    if near_miss < 1:
        defects.append("missing near-miss exemplar")
    if parent_valid is False:
        defects.append("dangling parent reference")
    return defects


def gate_definition_completeness(inputs: GateInputs, _profile: ResolvedProfile) -> GateResult:
    """U7: every code is fully specified (definition/criteria/exemplars/parent)."""
    doc = as_dict(inputs.codebook)
    categories = as_list(doc.get("categories"))
    if not doc or not categories:
        return GateResult(
            gate_id=GATE_DEFINITION_COMPLETENESS,
            status="not_evaluable",
            reason="no codebook document recorded (inputs.codebook is empty)",
        )
    codes: list[tuple[str, dict[str, object]]] = []
    malformed = 0
    for entry in categories:
        raw = as_dict(entry)
        code_id = as_str(raw.get("id")) or as_str(raw.get("code_id"))
        if not raw or code_id is None:
            malformed += 1
            continue
        codes.append((code_id, raw))
    if not codes:
        return GateResult(
            gate_id=GATE_DEFINITION_COMPLETENESS,
            status="not_evaluable",
            reason="invalid_output: codebook categories carry no usable code dicts",
        )
    code_ids = {code_id for code_id, _raw in codes}
    hierarchical = any(_parent_of(raw) for _code_id, raw in codes)
    defects_by_code: dict[str, list[str]] = {}
    for code_id, raw in codes:
        parent_valid: bool | None = None
        if hierarchical:
            parent = _parent_of(raw)
            parent_valid = (parent in code_ids) if parent else True  # no parent = root
        defects = _code_defects(raw, parent_valid)
        if defects:
            defects_by_code[code_id] = defects
    value = {
        "n_codes": len(codes),
        "hierarchical": hierarchical,
        "malformed_entries": malformed,
        "defects": defects_by_code,
    }
    if defects_by_code:
        return GateResult(
            gate_id=GATE_DEFINITION_COMPLETENESS,
            status="fail",
            reason=(
                f"{len(defects_by_code)}/{len(codes)} code(s) incompletely specified "
                "(v6 U7: definition, criteria, positive + near-miss exemplar, valid parent)"
            ),
            value=value,
        )
    return GateResult(
        gate_id=GATE_DEFINITION_COMPLETENESS,
        status="pass",
        reason=f"all {len(codes)} code(s) carry definition, criteria and exemplars",
        value=value,
    )


__all__ = ["gate_definition_completeness"]
