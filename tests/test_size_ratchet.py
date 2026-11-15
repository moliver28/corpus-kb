"""Tests for the per-file line ceilings in the size ratchet (momus F11).

The count ratchet (``files_over_250``) cannot see growth inside a file
that is already over the 250-line soft limit. The ``max_file_lines`` map
in scripts/quality-baselines.json closes that hole: every over-limit
file carries a ceiling recorded at its exact size, and the gate fails
when a capped file grows, disappears, or drops back under the limit
(the entry must go in that PR), or when an over-limit file has no
ceiling (record its current size).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.size_ratchet import (
    BASELINES_PATH,
    LINE_LIMIT,
    check_file_ceilings,
    measure_file_lines,
    read_ceilings,
)


def test_check_passes_when_every_file_is_at_its_ceiling() -> None:
    file_lines = {"src/a.py": 300, "src/b.py": 251, "scripts/c.py": 480}
    ceilings = {"src/a.py": 300, "src/b.py": 251, "scripts/c.py": 480}
    assert check_file_ceilings(file_lines, ceilings) == []


def test_check_fails_when_a_capped_file_grows() -> None:
    failures = check_file_ceilings({"src/a.py": 301}, {"src/a.py": 300})
    assert len(failures) == 1
    assert "src/a.py" in failures[0]
    assert "301" in failures[0]
    assert "300" in failures[0]


def test_check_fails_on_a_stale_entry_for_a_missing_file() -> None:
    failures = check_file_ceilings({"src/a.py": 300}, {"src/a.py": 300, "src/gone.py": 400})
    assert len(failures) == 1
    assert "src/gone.py" in failures[0]


def test_check_fails_when_a_capped_file_drops_under_the_limit() -> None:
    # The entry must be removed: at or under the limit the count ratchet
    # governs the file again, so a stale ceiling would let it regrow.
    failures = check_file_ceilings({"src/a.py": LINE_LIMIT}, {"src/a.py": 300})
    assert len(failures) == 1
    assert "src/a.py" in failures[0]
    assert "Remove" in failures[0]


def test_check_fails_on_an_over_limit_file_without_a_ceiling() -> None:
    failures = check_file_ceilings({"src/new.py": LINE_LIMIT + 1}, {})
    assert len(failures) == 1
    assert "src/new.py" in failures[0]


def test_check_ignores_under_limit_files_without_a_ceiling() -> None:
    assert check_file_ceilings({"src/small.py": 100}, {}) == []


def test_check_failures_come_out_sorted_for_stable_output() -> None:
    failures = check_file_ceilings({"src/z.py": 999, "src/a.py": 999}, {})
    assert failures == sorted(failures)
    assert "src/a.py" in failures[0]
    assert "src/z.py" in failures[1]


def test_read_ceilings_reads_the_map(tmp_path: Path) -> None:
    baselines = tmp_path / "quality-baselines.json"
    baselines.write_text(json.dumps({"max_file_lines": {"src/a.py": 300}}), encoding="utf-8")
    assert read_ceilings(baselines) == {"src/a.py": 300}


def test_read_ceilings_fails_clean_without_the_key(tmp_path: Path) -> None:
    baselines = tmp_path / "quality-baselines.json"
    baselines.write_text(json.dumps({"files_over_250": 1}), encoding="utf-8")
    with pytest.raises(SystemExit, match="max_file_lines"):
        read_ceilings(baselines)


def test_read_ceilings_rejects_a_non_integer_ceiling(tmp_path: Path) -> None:
    baselines = tmp_path / "quality-baselines.json"
    baselines.write_text(json.dumps({"max_file_lines": {"src/a.py": "300"}}), encoding="utf-8")
    with pytest.raises(SystemExit, match="positive integer"):
        read_ceilings(baselines)


def test_baseline_ceilings_match_the_live_tree() -> None:
    """Honesty lock: each ceiling equals the file's size in this tree.

    The map names exactly the files over the soft limit, and every
    ceiling equals the measured size, so the gate blocks growth from
    the committed baseline on.
    """
    file_lines = measure_file_lines()
    over = {rel: n for rel, n in file_lines.items() if n > LINE_LIMIT}
    ceilings = read_ceilings(BASELINES_PATH)
    assert set(ceilings) == set(over), (
        "max_file_lines must name exactly the files over "
        f"{LINE_LIMIT} lines (missing: {sorted(set(over) - set(ceilings))}, "
        f"stale: {sorted(set(ceilings) - set(over))})"
    )
    drifted = {
        rel: (ceiling, over[rel]) for rel, ceiling in ceilings.items() if ceiling != over[rel]
    }
    assert not drifted, f"ceilings drifted from the live sizes: {drifted}"
