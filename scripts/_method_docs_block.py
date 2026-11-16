"""The generated method-table block for docs/ANALYSIS-METHODS.md.

Render-only helpers for ``gen_method_register_docs.py`` (one module per
file so both stay under the 250-line cap). ``build_block`` emits the
register table, the guidance bullets, and the Backend proof section.
"""

from __future__ import annotations

import re
from typing import Any

START_MARKER = "<!-- amr-register:generated:start -->"
END_MARKER = "<!-- amr-register:generated:end -->"

_STATUS_LABEL = {
    "implemented_existing": "existing op",
    "implemented_new": "new op",
    "human_led": "human-led",
    "not_planned": "not planned",
}

_CODE_TOKEN = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")


def _code(token: str) -> str:
    """Format one token as code (Vale: code-looking words need backticks)."""
    return f"`{token}`"


def _prose(text: str) -> str:
    """Backtick snake_case tokens inside a prose field, keep the rest."""
    return _CODE_TOKEN.sub(lambda m: _code(m.group(0)), text)


def _input_label(token: str) -> str:
    """The docs label for one input kind (the LLM initialism stays upper)."""
    return "LLM" if token == "llm" else _code(token)


_ROUTING_AXES_FIELDS = (
    "task_type",
    "comparison_structure",
    "output_form",
    "evidence_basis",
    "statistical_rigor",
    "cost_tier",
)

_ROUTING_PROSE_FIELDS = (("Watch for", "failure_modes"), ("Limits", "scope_limits"))

# The wave-2 parity statement: the DuckDB-proven op families and the one
# declared numeric drift (the coding tables' DOUBLE mirror).
_BACKEND_PROOF = [
    "",
    "## Backend proof",
    "",
    "The analysis ops behind these methods run on Postgres and on DuckDB. "
    "The parity gate proves it on a migrated DuckDB file "
    "(`tests/test_backend_operation_parity.py`). The screens, classifiers, "
    "cluster report, keyphrase ranks, sentiment screen, active-learning "
    "rank, and NLI pair run on both engines. "
    "The deep surface gate pins the same envelope keys on every "
    "surface. The six graph reads run on Postgres (the AGE or PG store) "
    "and on DuckDB (the graph store).",
    "",
    "One caveat: DuckDB stores coding confidences as `DOUBLE`. The stored "
    "value can differ from Postgres in far decimals.",
]


def _progression_lines(
    method: dict[str, Any],
    names: dict[str, str],
    prereqs_by_id: dict[str, list[str]],
    next_by_id: dict[str, list[str]],
) -> list[str]:
    """The progression bullets for one method (novice-UX program, wave 1).

    The rung and the taught concepts come from the row; the Requires and
    Next bullets come from the derived ladder views, keyed by method id.
    Links backtick their tokens (the code-token convention).
    """
    lines: list[str] = []
    if method.get("difficulty"):
        lines.append(f"  - Difficulty: {_code(method['difficulty'])}.")
    concepts = method.get("concepts") or []
    if concepts:
        lines.append(f"  - Teaches: {', '.join(_code(concept) for concept in concepts)}.")
    prereqs = prereqs_by_id.get(method["id"]) or []
    if prereqs:
        lines += _link_bullets("Requires", prereqs, names)
    nxt = next_by_id.get(method["id"]) or []
    if nxt:
        lines += _link_bullets("Next", nxt, names)
    return lines


# One bullet spends one word on the label; each sentence stays inside the
# 20-word plain-language cap with margin for the period.
_LINK_BULLET_WORD_BUDGET = 17


def _link_bullets(label: str, links: list[str], names: dict[str, str]) -> list[str]:
    """Render id links as bullets; each bullet stays one short sentence."""
    lines: list[str] = []
    current: list[str] = []
    words = 0
    for link in links:
        text = f"{_code(link)} {names[link]}" if link in names else _code(link)
        if current and words + len(text.split()) > _LINK_BULLET_WORD_BUDGET:
            lines.append(f"  - {label}: {', '.join(current)}.")
            current, words = [], 0
        current.append(text)
        words += len(text.split())
    lines.append(f"  - {label}: {', '.join(current)}.")
    return lines


def _routing_lines(method: dict[str, Any]) -> list[str]:
    """The routing-axes bullets for one method (analysis-routing program).

    Renders nothing when the row carries no routing axes. A row with
    SOME axes but not all six names the method id and stops, instead of
    rendering a half-filled block.
    """
    present = [name for name in _ROUTING_AXES_FIELDS if method.get(name)]
    if not present:
        return []
    if len(present) != len(_ROUTING_AXES_FIELDS):
        raise SystemExit(
            f"method {method.get('id')}: partial routing axes ({', '.join(present)}). "
            "Set all six axes or none."
        )
    axes = (
        f"  - Routing axes: task {_code(method['task_type'])}, comparison "
        f"{_code(method['comparison_structure'])}, output {_code(method['output_form'])}, "
        f"evidence {_code(method['evidence_basis'])}, "
        f"rigor {_code(method['statistical_rigor'])}, cost tier {_code(method['cost_tier'])}."
    )
    lines = [axes]
    for label, field_name in _ROUTING_PROSE_FIELDS:
        value = method.get(field_name)
        if value:
            lines.append(f"  - {label}: {_prose(value)}")
    phrases = method.get("question_phrases") or []
    if phrases:
        sample = " / ".join(f'"{phrase}"' for phrase in phrases[:3])
        lines.append(f"  - Ask it: {sample}")
    follows = method.get("suggests") or []
    if follows:
        quoted = " / ".join('"' + item + '"' for item in follows)
        lines.append(f"  - Follow-ups: {quoted}")
    # The primary source carries the venue annotation: "URL (Venue Year)".
    sources = method.get("sources") or []
    if sources:
        lines.append(f"  - Evidence: {sources[0]}")
    return lines


def _table_lines(method: dict[str, Any]) -> list[str]:
    """The table-contract bullets for one method (novice-UX program, wave 2).

    Renders nothing when the row declares no table. The reserved
    ``model_rank`` order entry keeps the producing model's reply order.
    """
    table = method.get("output_table")
    if not table:
        return []
    parts: list[str] = []
    for column in table["columns"]:
        rendered = f"`{column['name']}` ({column['type']}"
        if column.get("unit"):
            rendered += f", {column['unit']}"
        parts.append(rendered + ")")
    order = table["order_by"]
    order_text = (
        "the model reply order"
        if order == ["model_rank"]
        else ", ".join(_code(key) for key in order)
    )
    head = f"  - Table: {', '.join(parts)}. Order: {order_text}."
    if table.get("precision") is not None:
        head += f" Round to {table['precision']} places."
    lines = [head]
    if table.get("ties"):
        lines.append(f"  - Ties: {_prose(table['ties'])}")
    denominators = table.get("denominators") or []
    if denominators:
        keys = ", ".join(_code(key) for key in denominators)
        lines.append(f"  - Shares divide by {keys}.")
    caveats = table.get("caveats") or []
    if caveats:
        joined = " ".join(_prose(caveat) for caveat in caveats)
        lines.append(f"  - Caveats: {joined}")
    return lines


def build_block(
    register: dict[str, Any],
    prereqs_by_id: dict[str, list[str]],
    next_by_id: dict[str, list[str]],
) -> str:
    """The generated markdown: the register table plus guidance bullets."""
    methods: list[dict[str, Any]] = register["methods"]
    names = {method["id"]: method["name"] for method in methods}
    lines = [
        START_MARKER,
        f"Register version: {register['register_version']}. Source: the "
        "research document named in `config/analysis_methods.yaml`.",
        "",
        "| Id | Method | Output family | Inputs | Calibration | Autonomy | "
        "Complexity | Difficulty | Status | Op |",
        "|----|--------|---------------|--------|-------------|----------|------------"
        "|------------|--------|----|",
    ]
    for method in methods:
        op = method.get("op") or "(none)"
        op_cell = _code(op) if op != "(none)" else op
        inputs = ", ".join(_input_label(i) for i in method["inputs_required"])
        difficulty = _code(method["difficulty"]) if method.get("difficulty") else "(none)"
        lines.append(
            f"| {method['id']} | {method['name']} | {_code(method['output_family'])} "
            f"| {inputs} | {_code(method['calibration_effort'])} "
            f"| {_code(method['autonomy'])} | {_code(method['complexity_tier'])} "
            f"| {difficulty} "
            f"| {_STATUS_LABEL[method['implementation_status']]} | {op_cell} |"
        )
    lines += ["", "## Method guidance", ""]
    for method in methods:
        op = method.get("op")
        suffix = f" (op: `{op}`)" if op else ""
        lines.append(
            f"- **{method['id']} — {method['name']}**{suffix}. {_prose(method['usage_guidance'])}"
        )
        lines.append(f"  - Validate: {_prose(method['validation_required'])}")
        lines.append(f"  - Claim only: {_prose(method['permitted_claims'])}")
        lines.append(f"  - Do not claim: {_prose(method['do_not_claim'])}")
        lines.append(
            f"  - Cautions: {_prose(method['robustness_caveat'])} "
            f"Bias: {_prose(method['bias_risk'])}"
        )
        lines += _progression_lines(method, names, prereqs_by_id, next_by_id)
        lines += _routing_lines(method)
        lines += _table_lines(method)
    lines += _BACKEND_PROOF
    lines.append(END_MARKER)
    return "\n".join(lines)
