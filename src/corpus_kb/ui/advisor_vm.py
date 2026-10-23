"""The method-advisor view model (the AMR program, UX panel).

The panel NEVER re-ranks: :func:`method_advisor` loads the register
through Agent A's loader and ranks through ``rank_methods`` (the ONE
selector rubric), so the browser shows the same rows the selector op
returns. This module shapes plain dicts and strings only — no NiceGUI
import — so tests drive it offline, exactly like
``ui/view_models.py`` and ``ui/demo_kb_vm.py``.

Honest statuses: ``empty`` (no task text), ``invalid`` (a constraint
value the rubric rejects; the note carries the remediation and the
valid values ride ``options``), ``unavailable`` (the register file
cannot be loaded). Success rows carry the honest-use fields verbatim:
rank, name, summary, rank_reason, missing_inputs, validation_required,
permitted_claims, do_not_claim.
"""

from __future__ import annotations

from typing import Any, cast

from corpus_kb.analysis.method_register import (
    INPUT_KINDS,
    MethodRecommendation,
    load_method_register,
)
from corpus_kb.analysis.method_register_rubric import (
    COMPLEXITY_TIERS,
    OUTPUT_FAMILIES,
    rank_methods,
)

__all__ = ["advisor_markdown", "method_advisor", "split_csv"]

ADVISOR_MAX_ROWS = 30

_EMPTY_NOTE = "Describe the analysis task first."
_NO_ROWS_NOTE = "No method matched. Remove a filter and try again."
_LOAD_FAILED_NOTE = "The method register could not be loaded. Read the note: "


def _options() -> dict[str, list[str]]:
    """The valid vocabularies, so the panel can show them on a bad value."""
    return {
        "output_families": list(OUTPUT_FAMILIES),
        "input_kinds": list(INPUT_KINDS),
        "complexity_tiers": list(COMPLEXITY_TIERS),
    }


def _empty_view(status: str, note: str) -> dict[str, Any]:
    return {
        "status": status,
        "note": note,
        "recommendations": [],
        "options": _options(),
    }


def _row(recommendation: MethodRecommendation) -> dict[str, object]:
    """One recommendation -> the panel row (descriptive axes verbatim)."""
    return {
        "rank": int(recommendation.rank),
        "name": str(recommendation.name),
        "summary": str(recommendation.summary),
        "rank_reason": str(recommendation.rank_reason),
        "missing_inputs": list(recommendation.missing_inputs),
        "validation_required": str(recommendation.validation_required),
        "permitted_claims": str(recommendation.permitted_claims),
        "do_not_claim": str(recommendation.do_not_claim),
    }


def split_csv(raw: str) -> list[str] | None:
    """A comma-separated box value -> clean strings; blank means None."""
    items = [item.strip() for item in str(raw).split(",")]
    cleaned = [item for item in items if item]
    return cleaned or None


async def method_advisor(
    task_description: str,
    config: dict[str, Any] | None = None,
    output_family: str | None = None,
    inputs_available: list[str] | None = None,
    max_complexity_tier: str | None = None,
) -> dict[str, Any]:
    """Rank the register for one stated task (the panel's whole flow).

    The task text is required (the rubric ranks methods, it does not
    guess one). One rubric call, rows capped at ``ADVISOR_MAX_ROWS``.
    """
    if not str(task_description).strip():
        return _empty_view("empty", _EMPTY_NOTE)
    try:
        register = load_method_register(config)
    except ValueError as exc:  # the register file is missing or malformed
        return _empty_view("unavailable", str(exc))
    except Exception as exc:  # a broken register must not kill the page
        return _empty_view("unavailable", f"{_LOAD_FAILED_NOTE}{exc}")
    try:
        ranked = rank_methods(
            register,
            output_family=output_family,
            inputs_available=inputs_available,
            max_complexity_tier=max_complexity_tier,
        )
    except ValueError as exc:  # a constraint value the rubric rejects
        return _empty_view("invalid", str(exc))
    rows = [_row(item) for item in ranked[:ADVISOR_MAX_ROWS]]
    note = "" if rows else _NO_ROWS_NOTE
    return {"status": "success", "note": note, "recommendations": rows, "options": _options()}


def advisor_markdown(view: dict[str, Any]) -> str:
    """The panel rendering: one block per ranked method, claims verbatim."""
    status = str(view.get("status", "unavailable"))
    note = str(view.get("note", "") or "")
    rows = cast("list[dict[str, object]]", view.get("recommendations", []))
    if status != "success" or not rows:
        return note or _NO_ROWS_NOTE
    blocks: list[str] = []
    for row in rows:
        missing = cast("list[str]", row["missing_inputs"])
        lines = [
            f"**Rank {row['rank']} — {row['name']}**",
            "",
            str(row["summary"]),
            "",
            f"- Why: {row['rank_reason']}",
            f"- Validate: {row['validation_required']}",
            f"- You may claim: {row['permitted_claims']}",
            f"- Do not claim: {row['do_not_claim']}",
        ]
        if missing:
            lines.append(f"- Missing inputs: {', '.join(missing)}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
