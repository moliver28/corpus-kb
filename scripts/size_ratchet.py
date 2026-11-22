"""Report-only size and complexity ratchet (PR-F, zero-drift rule 10).

The gate compares three things with the checked-in baselines in
``scripts/quality-baselines.json``:

1. ``PLR0915`` findings (ruff ``too-many-statements``) over ``src/``,
   ``scripts/``, and ``tests/``.
2. Files over the 250-line soft limit under ``src/`` and ``scripts/``.
3. Per-file line ceilings (``max_file_lines``): every file already over
   the soft limit is capped at its recorded size (momus F11), so an
   over-limit file cannot grow without failing this gate. ``plugins/``
   and the named ``tests/`` files (issue #543) carry the same ceilings:
   growth breaks, removal or shrink to the limit releases the entry.
   Those roots are ceiling-only — a ceiling there is enforced but never
   demanded, so the wide over-limit ``tests/`` cluster is not forced to
   file 160 ceilings in one PR.

A count above its baseline fails the build. A count at or below the
baseline passes, so the in-flight dedup PRs land without touching this
gate. A ceiling breaks when the file grows past it, when the file is
gone, or when the file has dropped to or under the limit (remove the
entry in that PR). An over-limit file with no ceiling also fails:
record its current size. Lower the baselines in the same PR that
shrinks the code; never raise one to make a failing PR pass.

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
# Scanned source roots: the package, the gate/generator scripts, and the
# tests (scripts/ joined the scan when its worst blocks were decomposed).
SCAN_ROOTS = ("src", "scripts", "tests")
LONG_ROOTS = ("src", "scripts")
# Ceiling-only roots (issue #543): a max_file_lines entry under these
# trees is enforced like any other, but no ceiling is demanded. The
# over-250 count stays scoped to LONG_ROOTS.
CEILING_ONLY_ROOTS = ("plugins/", "tests/")


def _load_baselines() -> dict[str, int | float]:
    data = json.loads(BASELINES_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        message = f"{BASELINES_PATH} must hold a JSON object."
        raise SystemExit(message)
    return data


def read_ceilings(baselines_path: Path = BASELINES_PATH) -> dict[str, int]:
    """Read the ``max_file_lines`` per-file ceiling map.

    Exits with a clear message when the key is missing or an entry is
    not a positive integer.
    """
    data = json.loads(baselines_path.read_text(encoding="utf-8"))
    ceilings = data.get("max_file_lines") if isinstance(data, dict) else None
    if not isinstance(ceilings, dict):
        message = f"{baselines_path} is missing the max_file_lines object."
        raise SystemExit(message)
    for rel, ceiling in ceilings.items():
        if not isinstance(ceiling, int) or isinstance(ceiling, bool) or ceiling < 1:
            message = f"max_file_lines[{rel!r}] must be a positive integer, got {ceiling!r}."
            raise SystemExit(message)
    return ceilings


def _count_plr0915() -> int:
    """Return the number of ruff PLR0915 findings over the scan roots."""
    command = [
        sys.executable,
        "-m",
        "ruff",
        "check",
        *SCAN_ROOTS,
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


def measure_file_lines() -> dict[str, int]:
    """Map each scanned Python file to its line count (repo-relative).

    LONG_ROOTS feed both the over-250 count and the ceiling check;
    CEILING_ONLY_ROOTS (``plugins/``, ``tests/``) feed the ceiling check
    only — a file there needs no ceiling unless one is recorded.
    """
    counts: dict[str, int] = {}

    def scan(root: str) -> None:
        for path in sorted((ROOT / root).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                counts[path.relative_to(ROOT).as_posix()] = sum(1 for _ in handle)

    for root in LONG_ROOTS:
        scan(root)
    for root in CEILING_ONLY_ROOTS:
        scan(root)
    return counts


def check_file_ceilings(
    file_lines: dict[str, int],
    ceilings: dict[str, int],
    ceiling_only_roots: tuple[str, ...] = CEILING_ONLY_ROOTS,
) -> list[str]:
    """Return one failure message per broken ceiling, sorted by path.

    A ceiling breaks when the file grew, when the file is gone, or when
    the file dropped to or under the line limit (the entry must go in
    that PR). Under the scanned long roots an over-limit file with no
    ceiling also fails: its current size must be recorded in the
    baseline. Under the ceiling-only roots a missing ceiling is fine —
    the entry is enforced when present, never demanded.
    """
    failures: list[str] = []
    for rel in sorted(set(file_lines) | set(ceilings)):
        ceiling = ceilings.get(rel)
        lines = file_lines.get(rel)
        ceiling_required = not rel.startswith(ceiling_only_roots)
        if ceiling is None:
            if ceiling_required and lines is not None and lines > LINE_LIMIT:
                failures.append(
                    f"FAIL: {rel} is over {LINE_LIMIT} lines with no ceiling. "
                    f"Record its current size ({lines}) in max_file_lines."
                )
            continue
        if lines is None:
            failures.append(
                f"FAIL: max_file_lines lists {rel}, but the file does not exist. Remove the entry."
            )
            continue
        if lines <= LINE_LIMIT:
            failures.append(
                f"FAIL: {rel} is now {lines} lines, at or under the limit. "
                "Remove its ceiling entry; the count ratchet governs it now."
            )
            continue
        if lines > ceiling:
            failures.append(
                f"FAIL: {rel} grew to {lines} lines. Its ceiling is {ceiling}. "
                "Split the file or shrink the code."
            )
    return failures


def main() -> int:
    baselines = _load_baselines()
    plr_baseline = int(baselines["plr0915"])
    long_baseline = int(baselines["files_over_250"])
    ceilings = read_ceilings(BASELINES_PATH)

    plr_count = _count_plr0915()
    print(f"PLR0915 findings: {plr_count} (baseline {plr_baseline})")
    file_lines = measure_file_lines()
    over = {
        rel: n
        for rel, n in file_lines.items()
        if n > LINE_LIMIT and not rel.startswith(CEILING_ONLY_ROOTS)
    }
    long_count = len(over)
    print(f"Files over {LINE_LIMIT} lines: {long_count} (baseline {long_baseline})")
    for rel in sorted(over):
        print(f"  over {LINE_LIMIT} lines: {rel} ({over[rel]})")
    print(f"Per-file ceilings: {len(ceilings)} files capped (max_file_lines)")

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
    for message in check_file_ceilings(file_lines, ceilings):
        print(message)
        failed = True
    if not failed:
        print(
            "Size ratchet: PASS (report-only ratchet; baselines in scripts/quality-baselines.json)"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
