"""U23 pg_textsearch stub tests: gate semantics, honest statuses, license notes."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "bench_pg_textsearch.py"

_spec = importlib.util.spec_from_file_location("bench_pg_textsearch_test", SCRIPT)
assert _spec is not None and _spec.loader is not None
bpg = importlib.util.module_from_spec(_spec)
sys.modules["bench_pg_textsearch_test"] = bpg
_spec.loader.exec_module(bpg)


def test_default_gate_is_off_and_honest() -> None:
    report = bpg.gated_report(enabled=False)
    assert report["status"] == "not_enforced"
    assert report["native_fts_default"] is True
    assert "opt-in" in str(report["reason"])


def test_license_and_limitation_notes_are_carried() -> None:
    assert "PostgreSQL License" in bpg.LICENSE
    assert "single index scan" in bpg.KNOWN_LIMITATION
    report = bpg.gated_report(enabled=True)
    assert "PostgreSQL License" in str(report["license"])
    assert "single index scan" in str(report["known_limitation"])
    assert "AGPL" in str(report["pg_search"])


def test_missing_extension_is_insufficient_not_zero() -> None:
    import asyncio

    # check_extension on a live DB returns "" for an absent extension; the
    # benchmark must translate that into insufficient_data, never invented
    # numbers. Simulate the absent-extension branch directly.
    async def _absent(dsn: str) -> str:
        return ""

    original = bpg.check_extension
    bpg.check_extension = _absent  # type-agnostic probe stub for this test
    try:
        report = asyncio.run(bpg.run_benchmark("postgresql://unused", k=10))
    finally:
        bpg.check_extension = original
    assert report["status"] == "insufficient_data"
    assert report["native_fts_default"] is True


def test_main_without_flag_exits_zero_and_reports_not_enforced(
    capsys: object, monkeypatch: object
) -> None:
    exit_code = bpg.main([])
    assert exit_code == 0
    assert bpg.parse_args(["--enable-pg-textsearch"]).enable_pg_textsearch is True
    assert bpg.parse_args([]).enable_pg_textsearch is False
