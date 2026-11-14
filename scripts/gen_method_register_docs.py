#!/usr/bin/env python3
"""Generate the method table block in docs/ANALYSIS-METHODS.md (AMR program).

Reads the register YAML (``config/analysis_methods.yaml``, the repo truth)
and rewrites ONLY the block between the exact markers below; the static
header around it is hand-maintained:

  <!-- amr-register:generated:start -->
  ...the table plus the per-method guidance bullets...
  <!-- amr-register:generated:end -->

``--check`` regenerates in memory and exits 1 on drift (the license-lock
and harness-pack precedent) so ``poe gen`` / ``poe check`` catch a stale
docs table after any register edit.

Usage:
    python scripts/gen_method_register_docs.py [--check] [--project-root PATH]

Exit code 0 = in sync (or written), 1 = drift detected.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml

REGISTER_REL = Path("config") / "analysis_methods.yaml"
DOCS_REL = Path("docs") / "ANALYSIS-METHODS.md"
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

_ROUTING_PROSE_FIELDS = (
    ("Watch for", "failure_modes"),
    ("Limits", "scope_limits"),
)

_PROGRESSION_FIELDS = ("difficulty", "concepts", "prerequisites", "next")


def _progression_lines(method: dict[str, Any], names: dict[str, str]) -> list[str]:
    """The progression bullets for one method (novice-UX program, wave 1).

    Renders nothing when the row carries no progression fields (all four
    are optional), so old registers and test fixtures still generate
    cleanly. Each present field renders one line; concepts and method-id
    links backtick their tokens (the code-token convention).
    """
    present = [name for name in _PROGRESSION_FIELDS if method.get(name) not in (None, [])]
    if not present:
        return []
    lines: list[str] = []
    if method.get("difficulty"):
        lines.append(f"  - Difficulty: {_code(method['difficulty'])}.")
    concepts = method.get("concepts") or []
    if concepts:
        lines.append(f"  - Teaches: {', '.join(_code(concept) for concept in concepts)}.")
    prereqs = method.get("prerequisites") or []
    if prereqs:
        needs = ", ".join(_link_text(link, names) for link in prereqs)
        lines.append(f"  - Requires: {needs}.")
    nxt = method.get("next") or []
    if nxt:
        steps = ", ".join(_link_text(link, names) for link in nxt)
        lines.append(f"  - Next: {steps}.")
    return lines


def _link_text(link: str, names: dict[str, str]) -> str:
    """One method-id link as `id` plus the registered name."""
    name = names.get(link)
    return f"{_code(link)} {name}" if name else _code(link)


def _routing_lines(method: dict[str, Any]) -> list[str]:
    """The routing-axes bullets for one method (analysis-routing program).

    Renders nothing when the row carries no routing axes (all fields are
    optional), so old registers and test fixtures still generate cleanly.
    A row with SOME axes but not all six is an authoring bug: it names the
    method id and stops, instead of rendering a half-filled block.
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
        f"evidence {_code(method['evidence_basis'])}, rigor "
        f"{_code(method['statistical_rigor'])}, cost tier {_code(method['cost_tier'])}."
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


def build_block(register: dict[str, Any]) -> str:
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
        lines += _progression_lines(method, names)
        lines += _routing_lines(method)
    lines.append(END_MARKER)
    return "\n".join(lines)


def load_register(project_root: Path) -> dict[str, Any]:
    """Parse the register YAML (the packaged mirror mirrors it byte-for-byte)."""
    raw: Any = yaml.safe_load((project_root / REGISTER_REL).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "methods" not in raw:
        raise SystemExit(f"register YAML has no methods block: {project_root / REGISTER_REL}")
    return raw


def regenerate(project_root: Path, check: bool) -> int:
    """Rewrite the generated block, or report drift under --check."""
    docs_path = project_root / DOCS_REL
    text = docs_path.read_text(encoding="utf-8")
    start = text.index(START_MARKER)
    end = text.index(END_MARKER) + len(END_MARKER)
    generated = build_block(load_register(project_root))
    updated = text[:start] + generated + text[end:]
    if check:
        if text == updated:
            return 0
        print(f"drift: {DOCS_REL} does not match {REGISTER_REL}", file=sys.stderr)
        return 1
    docs_path.write_text(updated, encoding="utf-8")
    print(f"wrote {docs_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 on drift, write nothing")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    return regenerate(args.project_root, args.check)


if __name__ == "__main__":
    raise SystemExit(main())
