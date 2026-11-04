"""Tests for the warning ratchet gate (issue #294, zero-drift rule 10).

The gate counts the live suite's warnings through pytest's own
``pytest_warning_recorded`` hook and compares the count with the
``pytest_warnings`` ceiling in scripts/quality-baselines.json. Pytest 9
dropped the junit XML warnings attribute, so the hook is the honest
count channel.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest

from scripts.warning_ratchet import WarningCounter, compare, read_ceiling


def _recorded(message: str) -> warnings.WarningMessage:
    """One real WarningMessage object captured through the warnings module."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        warnings.warn(message, FutureWarning, stacklevel=1)
    return caught[0]


def test_read_ceiling_returns_the_pytest_warnings_number(tmp_path: Path) -> None:
    baselines = tmp_path / "quality-baselines.json"
    baselines.write_text(json.dumps({"pytest_warnings": 0}), encoding="utf-8")
    assert read_ceiling(baselines) == 0
    baselines.write_text(json.dumps({"pytest_warnings": 7}), encoding="utf-8")
    assert read_ceiling(baselines) == 7


def test_read_ceiling_fails_clean_without_the_key(tmp_path: Path) -> None:
    baselines = tmp_path / "quality-baselines.json"
    baselines.write_text(json.dumps({"coverage_floor": 87.5}), encoding="utf-8")
    with pytest.raises(SystemExit, match="pytest_warnings"):
        read_ceiling(baselines)


def test_counter_counts_every_recorded_warning() -> None:
    counter = WarningCounter()
    counter.pytest_warning_recorded(
        _recorded("first"), "runtest", "tests/test_a.py::test_one", None
    )
    counter.pytest_warning_recorded(
        _recorded("second"), "collect", "", ("tests/test_b.py", 3, "<module>")
    )
    assert counter.count == 2
    assert "FutureWarning" in counter.entries[0]
    assert "first" in counter.entries[0]


def test_compare_fails_only_above_the_ceiling() -> None:
    above = compare(3, 0)
    assert above is not None
    assert "3" in above
    assert "0" in above
    assert compare(0, 0) is None
    assert compare(2, 5) is None


def test_counter_sees_a_real_warning_through_a_pytest_run(tmp_path: Path) -> None:
    """End to end: one inner pytest session, one warned test, count 1."""
    suite = tmp_path / "test_warns.py"
    suite.write_text(
        "import warnings\n\n\ndef test_warns():\n    warnings.warn('probe', FutureWarning)\n",
        encoding="utf-8",
    )
    counter = WarningCounter()
    exit_code = pytest.main(
        [str(suite), "-q", "-p", "no:cacheprovider", "-p", "no:asyncio"],
        plugins=[counter],
    )
    assert exit_code == 0
    assert counter.count == 1


def test_counter_stays_zero_on_a_clean_pytest_run(tmp_path: Path) -> None:
    suite = tmp_path / "test_clean.py"
    suite.write_text("def test_clean():\n    assert True\n", encoding="utf-8")
    counter = WarningCounter()
    exit_code = pytest.main(
        [str(suite), "-q", "-p", "no:cacheprovider", "-p", "no:asyncio"],
        plugins=[counter],
    )
    assert exit_code == 0
    assert counter.count == 0
