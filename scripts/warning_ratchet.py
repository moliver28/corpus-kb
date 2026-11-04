"""Warning ratchet (issue #294, zero-drift rule 10).

The gate counts the live suite's warnings through pytest's own
``pytest_warning_recorded`` hook, so the count matches the number the
pytest summary prints. Pytest 9 dropped the junit XML warnings
attribute, so a report file cannot carry the count.

The count is compared with the ``pytest_warnings`` ceiling in
``scripts/quality-baselines.json``. A count above the ceiling fails the
build; a count at or below it passes. The ceiling stays 0 (issue #293
measured 0 in the pinned environment; the W11 pinned-env runs
re-measured 0 on 2026-11-04).

Pinned measurement environment (issue #294): ``uv pip install -e
".[dev,duckdb,inductive]"`` with Ollama UP. The count is not comparable
without the inductive extra, because umap/hdbscan-dependent tests skip.

Stdlib plus pytest (the dev extra pins it).
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASELINES_PATH = ROOT / "scripts" / "quality-baselines.json"
SUITE_ARGS = ["tests/", "-q", "--tb=short", "-p", "no:cacheprovider"]


def read_ceiling(path: Path = BASELINES_PATH) -> int:
    """Return the pytest_warnings ceiling from the baselines file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "pytest_warnings" not in data:
        message = f"{path} must hold a pytest_warnings number."
        raise SystemExit(message)
    return int(data["pytest_warnings"])


class WarningCounter:
    """Count every warning pytest records during one session."""

    def __init__(self) -> None:
        self.count = 0
        self.entries: list[str] = []

    def pytest_warning_recorded(
        self,
        warning_message: warnings.WarningMessage,
        when: str,
        nodeid: str,
        location: tuple[str, int, str] | None,
    ) -> None:
        """Hook: pytest calls this once per recorded warning."""
        self.count += 1
        category = getattr(warning_message.category, "__name__", "Warning")
        origin = location[0] if location else (nodeid or "<session>")
        self.entries.append(f"{category}: {warning_message.message} ({origin}, {when})")


def compare(count: int, ceiling: int) -> str | None:
    """Return a one-line fail reason above the ceiling, else None."""
    if count > ceiling:
        return (
            f"FAIL: {count} pytest warnings are above the ceiling {ceiling}. "
            "Fix the warnings, or lower the ceiling in the PR that owns them."
        )
    return None


def main() -> int:
    ceiling = read_ceiling()
    counter = WarningCounter()
    suite_exit = pytest.main([*SUITE_ARGS], plugins=[counter])
    print(f"pytest warnings: {counter.count} (ceiling {ceiling})")
    if suite_exit != 0:
        print(
            "FAIL: the suite did not pass, so the warning count is not "
            "meaningful. Fix the failing tests first."
        )
        return int(suite_exit)
    reason = compare(counter.count, ceiling)
    if reason is not None:
        for entry in counter.entries[:10]:
            print(f"  {entry}")
        if len(counter.entries) > 10:
            print(f"  ... and {len(counter.entries) - 10} more")
        print(reason)
        return 1
    print("Warning ratchet: PASS (ceiling in scripts/quality-baselines.json)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
