"""Overall coverage floor gate (PR-F, zero-drift rule 10).

Reads ``coverage.xml`` (produced by ``poe test-cov`` or by the CI
test-ubuntu suite run) and fails when the total line rate drops under
``coverage_floor`` in ``scripts/quality-baselines.json``.

The floor sits 0.5 points under the coverage measured at adoption so
normal drift does not flake. Raise the floor in the same PR that adds
tests. Lowering it needs a written reason in that PR.

Stdlib only.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINES_PATH = ROOT / "scripts" / "quality-baselines.json"
COVERAGE_XML = ROOT / "coverage.xml"


def _read_measured_percent() -> float:
    if not COVERAGE_XML.exists():
        message = (
            f"{COVERAGE_XML.name} is missing. Run `poe test-cov` first, "
            "or run the CI suite step that writes it."
        )
        raise SystemExit(message)
    root = ET.parse(COVERAGE_XML).getroot()
    raw = root.get("line-rate", "0")
    return round(float(raw) * 100, 2)


def main() -> int:
    baselines = json.loads(BASELINES_PATH.read_text(encoding="utf-8"))
    floor = float(baselines["coverage_floor"])
    measured = _read_measured_percent()
    print(f"Coverage: measured {measured:.2f} percent, floor {floor:.2f} percent.")
    if measured + 1e-9 < floor:
        print(
            "FAIL: coverage is under the floor. Add tests for the new "
            "code, or lower coverage_floor with a written reason in the PR."
        )
        return 1
    print("Coverage floor: PASS.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
