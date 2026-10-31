"""Report-only size and complexity ratchet (PR-F, zero-drift rule 10).

The gate counts two things and compares them with the checked-in
baselines in ``scripts/quality-baselines.json``:

1. ``PLR0915`` findings (ruff ``too-many-statements``) over ``src/`` and
   ``tests/``.
2. Files over the 250-line soft limit under ``src/``.

A count above its baseline fails the build. A count at or below the
baseline passes, so the in-flight dedup PRs land without touching this
gate. Lower the baselines in the same PR that shrinks the code; never
raise one to make a failing PR pass.

Stdlib only. Ruff runs through the interpreter that runs this script
(``python -m ruff``), so the gate uses the same ruff the dev extra pins.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINES_PATH = ROOT / "scripts" / "quality-baselines.json"
LINE_LIMIT = 250


def _load_baselines() -> dict[str, int | float]:
    data = json.loads(BASELINES_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        message = f"{BASELINES_PATH} must hold a JSON object."
        raise SystemExit(message)
    return data


def _count_plr0915() -> int:
    """Return the number of ruff PLR0915 findings over src/ and tests/."""
    command = [
        sys.executable,
        "-m",
        "ruff",
        "check",
        "src/",
        "tests/",
        "--select",
        "PLR0915",
        "--output-format",
        "json",
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    # ruff exits 1 when it finds violations. That is expected here, so
    # only a crashed run (no JSON at all) is an error.
    try:
        findings = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(result.stdout, end="")
        print(result.stderr, end="")
        message = "ruff did not return JSON. Is the dev extra installed?"
        raise SystemExit(message) from None
    if not isinstance(findings, list):
        message = "ruff returned unexpected JSON."
        raise SystemExit(message)
    return len(findings)


def _count_files_over_limit() -> int:
    """Return how many src/ Python files pass the 250-line soft limit."""
    over: list[Path] = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            lines = sum(1 for _ in handle)
        if lines > LINE_LIMIT:
            over.append(path)
            print(f"  over {LINE_LIMIT} lines: {path.relative_to(ROOT)} ({lines})")
    return len(over)


def main() -> int:
    baselines = _load_baselines()
    plr_baseline = int(baselines["plr0915"])
    long_baseline = int(baselines["files_over_250"])

    plr_count = _count_plr0915()
    print(f"PLR0915 findings: {plr_count} (baseline {plr_baseline})")
    long_count = _count_files_over_limit()
    print(f"Files over {LINE_LIMIT} lines: {long_count} (baseline {long_baseline})")

    failed = False
    if plr_count > plr_baseline:
        print(
            f"FAIL: PLR0915 count {plr_count} is above the baseline "
            f"{plr_baseline}. Split the new function, or lower the "
            "baseline in the same PR that shrinks the code."
        )
        failed = True
    if long_count > long_baseline:
        print(
            f"FAIL: {long_count} files pass {LINE_LIMIT} lines, above the "
            f"baseline {long_baseline}. Split the new file, or lower the "
            "baseline in the same PR that shrinks the code."
        )
        failed = True
    if not failed:
        print("Size ratchet: PASS (report-only ratchet; baselines in scripts/quality-baselines.json)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
