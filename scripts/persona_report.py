"""Report assembly for the persona meter walk (issue #441).

``scripts/persona_meter.py`` produces the walk; this module turns the
walk into the versioned report dict (``schema.v1.json``) and writes it.
The committed report carries no wall times: the gate path calls
``build_report(..., include_wall=False)``, so the same tree yields the
same report bytes.

``rung_grades`` restates the six v1 close-out grades (Setup B+, Ingest
B+, Ask B+, Explore B-, Analyze A-, Report A-) as provenance authored
in this PR. The v1 session notes are untracked, so no in-tree source
exists to cite. Grades stay human-applied labels; the meter emits
numbers only.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from corpus_kb._setup.doctor_journey import build_journey_checks
from corpus_kb.analysis.method_register import MethodRegister
from scripts.persona_meter import WALK_CONFIG, WalkPhase, WalkReport, run_walk

__all__ = ["DOCTOR_GRADES", "build_report", "cli", "doctor_next_step", "write_report"]

REPORT_VERSION = "1.0"
METER_NAME = "persona-meter"
RECIPE_NAME = "novice_default"

# The six v1 close-out rung grades, restated as provenance authored in
# this PR (the v1 session notes are untracked). Human labels, never
# computed by the meter.
DOCTOR_GRADES: dict[str, str] = {
    "setup": "B+",
    "ingest": "B+",
    "ask": "B+",
    "explore": "B-",
    "analyze": "A-",
    "report": "A-",
}


def doctor_next_step(phases: list[WalkPhase], method_id: str) -> int | None:
    """The first phase that recommends this method, else ``None``."""
    for phase in phases:
        row = phase.recommended
        if row is not None and row.id == method_id:
            return phase.phase
    return None


def _journey_detail(register: MethodRegister, phase: WalkPhase) -> str:
    """The doctor's state and next-step sentences for one phase."""
    checks = build_journey_checks(register, phase.state, ollama_ok=False)
    return " ".join(check.detail for check in checks)


def build_report(
    register: MethodRegister, walk: WalkReport, include_wall: bool
) -> dict[str, object]:
    """The report dict per ``schema.v1.json``. Stable keys, sorted ids.

    ``include_wall=False`` (the committed and gate path) omits
    ``wall_ms`` so the committed bytes stay deterministic.
    """
    methods: dict[str, object] = {}
    for method_id in walk.order:
        metrics = walk.metrics[method_id]
        record: dict[str, object] = {
            "phrase_resolution_steps": metrics.phrase_resolution_steps,
            "failed_attempts": metrics.failed_attempts,
            "override_needed": metrics.override_needed,
            "success": metrics.success,
            "doctor_next_step": doctor_next_step(walk.phases, method_id),
        }
        if include_wall:
            record["wall_ms"] = metrics.wall_ms
        methods[method_id] = record
    return {
        "schema_version": REPORT_VERSION,
        "meter": METER_NAME,
        "recipe": RECIPE_NAME,
        "rung_grades": dict(DOCTOR_GRADES),
        "novice_walk_order": list(walk.order),
        "doctor_journey": {
            f"phase_{phase.phase}": _journey_detail(register, phase) for phase in walk.phases
        },
        "methods": methods,
    }


def write_report(path: Path, report: dict[str, object]) -> None:
    """Write the report JSON: sorted keys, two-space indent, trailing newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def cli(argv: list[str]) -> int:
    """Run one walk and write the report. Flags: ``--write <path>``,
    ``--report-wall`` (local diagnosis only; the poe gate never passes
    it). Exit 0 on a written report, 2 on a bad flag.
    """
    write_path: Path | None = None
    include_wall = False
    rest = list(argv)
    while rest:
        flag = rest.pop(0)
        if flag == "--write" and rest:
            write_path = Path(rest.pop(0))
        elif flag == "--report-wall":
            include_wall = True
        else:
            print(f"Unknown flag: {flag}. Use --write <path> or --report-wall.")
            return 2
    from corpus_kb.analysis.method_register_loader import load_method_register

    register = load_method_register({})
    walk = asyncio.run(run_walk(register, WALK_CONFIG))
    report = build_report(register, walk, include_wall=include_wall)
    if write_path is None:
        print(json.dumps(report, sort_keys=True, indent=2))
        return 0
    write_report(write_path, report)
    print(f"Persona meter report written: {write_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover - local diagnosis entry point
    raise SystemExit(cli(sys.argv[1:]))
