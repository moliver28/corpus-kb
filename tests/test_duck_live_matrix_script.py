"""Offline drift pins for scripts/duck_live_matrix.py (parity PR-8).

The live matrix runs off-peak against a real DuckDB file; these pins
run offline in every CI leg: the script's op list must equal the CI
parity tuple plus the HTTP-only upload op (no op can silently miss its
live proof), and the --list mode must answer without opening a store.
"""

from __future__ import annotations

import json

import pytest
from test_backend_operation_parity import PARITY_OPS

from scripts.duck_live_matrix import OPS, list_ops, main


def test_live_matrix_lists_all_parity_ops() -> None:
    """The script's op list equals PARITY_OPS plus ingest_upload (drift pin).

    The upload op has no PARITY_OPS row (it is HTTP-only by catalogue
    ruling), so the live matrix carries it as the extra entry.
    """
    expected = set(PARITY_OPS) | {"ingest_upload"}
    assert set(OPS) == expected, f"matrix drift: {sorted(expected ^ set(OPS))}"
    assert len(OPS) == len(set(OPS)), "the matrix lists an op twice"
    assert list_ops() == list(OPS), "list_ops() must answer the declared order"


def test_live_matrix_dry_run_offline(capsys: pytest.CaptureFixture[str]) -> None:
    """--list answers the op list as JSON without opening any store."""
    code = main(["--list"])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert code == 0
    assert payload["mode"] == "list"
    assert payload["ops"] == list_ops()
