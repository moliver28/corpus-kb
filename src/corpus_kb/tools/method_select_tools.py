"""MCP/HTTP/socket/CLI handler for the method selector (AMR program).

:func:`analysis_select_method` — a THIN envelope wrapper over
:func:`corpus_kb.analysis.method_register_rubric.rank_methods` (the ONE
rubric; this module never re-scores). It validates the request against
the register's controlled vocabularies, loads the register through the
one loader, ranks, and returns the recommendations verbatim.

No heavy imports at module level (the P0 rule): the chain here is
pydantic + yaml only — the register loader and the rubric are registry
logic, not model code. ``pg_pool`` is accepted for the house handler
signature; the selector reads no database.
"""

from __future__ import annotations

import asyncpg

from ..analysis.method_register import (
    COMPLEXITY_TIERS,
    INPUT_KINDS,
    OUTPUT_FAMILIES,
    load_method_register,
)
from ..analysis.method_register_rubric import rank_methods
from ..errors import ValidationError
from .ingest_common import DEFAULT_TENANT_ID

__all__ = ["analysis_select_method"]

_EMPTY_TASK_REASON = "task_description is empty."
_EMPTY_TASK_REMEDY = "State the research task in one sentence, then retry."


def _vocab_error(name: str, value: str, allowed: tuple[str, ...]) -> ValidationError:
    """One typed vocabulary rejection with the valid values named."""
    return ValidationError(
        f"{name}: {value!r} is not supported. Supported: {', '.join(allowed)}.",
        remediation=f"Use one of the supported values: {', '.join(allowed)}.",
    )


def _check_request(
    task_description: str,
    output_family: str | None,
    inputs_available: list[str] | None,
    max_complexity_tier: str | None,
) -> None:
    """Typed request checks (validation_error family: 400 / -32602 / exit 1)."""
    if not isinstance(task_description, str) or not task_description.strip():
        raise ValidationError(_EMPTY_TASK_REASON, remediation=_EMPTY_TASK_REMEDY)
    if output_family is not None and output_family not in OUTPUT_FAMILIES:
        raise _vocab_error("output_family", output_family, OUTPUT_FAMILIES)
    if max_complexity_tier is not None and max_complexity_tier not in COMPLEXITY_TIERS:
        raise _vocab_error("max_complexity_tier", max_complexity_tier, COMPLEXITY_TIERS)
    if inputs_available is not None:
        unknown = [kind for kind in inputs_available if kind not in INPUT_KINDS]
        if unknown:
            raise _vocab_error("inputs_available", ", ".join(unknown), INPUT_KINDS)


async def analysis_select_method(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    *,
    task_description: str,
    output_family: str | None = None,
    inputs_available: list[str] | None = None,
    max_complexity_tier: str | None = None,
) -> dict[str, object]:
    """Rank register methods for the stated task; pure registry logic.

    Returns ``{status, register_version, recommendations}``. Each
    recommendation row is the register record verbatim plus ``rank``,
    ``rank_reason``, and ``missing_inputs`` (the rubric's fixed order:
    output family, input coverage, complexity tier, calibration effort,
    selection priority, id). Deterministic; no LLM, no database read.
    """
    del pg_pool, tenant_id  # accepted for the house signature; reads no database
    _check_request(task_description, output_family, inputs_available, max_complexity_tier)
    register = load_method_register(config)
    ranked = rank_methods(register, output_family, inputs_available, max_complexity_tier)
    return {
        "status": "success",
        "register_version": register.register_version,
        "recommendations": [record.model_dump(mode="json") for record in ranked],
    }
