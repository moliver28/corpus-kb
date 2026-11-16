"""The persona meter gate (issue #441, zero-drift rule 10).

Runs the offline novice walk (``scripts/persona_meter.py``) and compares
the gated report fields with the committed baseline
``tests/fixtures/persona_meter/report.v1.json``. Only
``phrase_resolution_steps`` gates, as a per-method ceiling: the gate
fails when the live walk exceeds it and passes when it improves. Wall
time, failed attempts, the walk order flag, and the doctor strings are
report-only. No database, no Ollama, no network.

The baseline paths ride the ``persona_meter`` block in
``scripts/quality-baselines.json``. Numbers only move the honest way:
re-measure the fixture in the PR that changes the phrases.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from corpus_kb.analysis.method_register_loader import load_method_register
from scripts.persona_meter import WALK_CONFIG, run_walk
from scripts.persona_report import build_report

__all__ = ["compare_reports", "main", "read_baselines"]

ROOT = Path(__file__).resolve().parents[1]
BASELINES_PATH = ROOT / "scripts" / "quality-baselines.json"
_MAX_FAILURES = 20


def read_baselines(path: Path = BASELINES_PATH) -> dict[str, str]:
    """The ``persona_meter`` block: report path plus schema path."""
    data = json.loads(path.read_text(encoding="utf-8"))
    block = data.get("persona_meter") if isinstance(data, dict) else None
    if (
        not isinstance(block, dict)
        or not isinstance(block.get("report_path"), str)
        or not isinstance(block.get("schema_path"), str)
    ):
        raise SystemExit(f"{path} is missing the persona_meter block (report_path, schema_path).")
    return block


def _steps_failures(
    method_id: str, field: str, expected: object, actual: object, direction: str
) -> list[str]:
    """The gated numeric comparison for one method record field."""
    if not isinstance(expected, (int, float)) or not isinstance(actual, (int, float)):
        message = f"FAIL: method {method_id} {field} is missing from the live report."
        return [message]
    over = actual > expected if direction == "ceiling" else actual < expected
    if not over:
        return []
    word = "exceeds ceiling" if direction == "ceiling" else "is under floor"
    return [
        f"FAIL: method {method_id} {field} {actual} {word} {expected}. "
        "Split the recipe, or re-measure the fixture honestly in the same PR."
    ]


def compare_reports(
    expected: dict[str, object], actual: dict[str, object], schema: dict[str, object]
) -> list[str]:
    """One failure message per gated regression; empty means pass.

    Gated: per-method ``phrase_resolution_steps`` ceilings, method
    presence, the record shape (no undeclared fields), and the walk
    order names in ``walk_gated``. Everything else is report-only.
    """
    failures: list[str] = []
    fields = schema.get("fields") if isinstance(schema.get("fields"), dict) else {}
    declared = set(fields)
    for name in schema.get("walk_gated", []):
        if expected.get(name) != actual.get(name):
            failures.append(
                f"FAIL: {name} changed. Re-measure the fixture honestly in the same PR."
            )
    expected_methods = expected.get("methods")
    actual_methods = actual.get("methods")
    if not isinstance(expected_methods, dict) or not isinstance(actual_methods, dict):
        failures.append("FAIL: the baseline report is missing its methods object.")
        return sorted(failures)[:_MAX_FAILURES]
    for method_id in sorted(expected_methods):
        if method_id not in actual_methods:
            failures.append(f"FAIL: method {method_id} is missing from the live walk.")
            continue
        baseline_record = expected_methods[method_id]
        live_record = actual_methods[method_id]
        if not isinstance(baseline_record, dict) or not isinstance(live_record, dict):
            failures.append(f"FAIL: method {method_id} carries a malformed record.")
            continue
        for field in sorted(set(live_record) - declared):
            failures.append(f"FAIL: method {method_id} carries an undeclared field: {field}.")
        for field, spec in sorted(fields.items()):
            if not isinstance(spec, dict) or not spec.get("gated"):
                continue
            if field not in baseline_record:
                continue
            direction = str(spec.get("direction", "ceiling"))
            failures.extend(
                _steps_failures(
                    method_id, field, baseline_record[field], live_record.get(field), direction
                )
            )
    return sorted(failures)[:_MAX_FAILURES]


def main() -> int:
    """The gate entry: walk, build, compare, print, exit."""
    block = read_baselines()
    schema_path = ROOT / block["schema_path"]
    report_path = ROOT / block["report_path"]
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    if not report_path.exists():
        print(f"FAIL: baseline report is missing: {block['report_path']}")
        return 1
    baseline = json.loads(report_path.read_text(encoding="utf-8"))
    shaped = isinstance(baseline, dict)
    shaped = shaped and "methods" in baseline and "novice_walk_order" in baseline
    if not shaped:
        print(f"FAIL: baseline report is missing: {block['report_path']} has no measured walk yet.")
        return 1
    register = load_method_register({})
    walk = asyncio.run(run_walk(register, WALK_CONFIG))
    actual = build_report(register, walk, include_wall=False)
    failures = compare_reports(baseline, actual, schema)
    for message in failures:
        print(message)
    if failures:
        return 1
    print(f"Persona meter: PASS (ceilings in {block['report_path']})")
    return 0


if __name__ == "__main__":  # pragma: no cover - the poe gate entry point
    raise SystemExit(main())
