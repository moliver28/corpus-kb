"""Config-backend parity gate tests (parity program PR-9, rule 12).

The heart runs the gate logic in-process against the REAL defaults and
the real matrix: any default leaf without a declaration row, any stale
row, any reader without its grep token, or any refusal row without its
``tests/test_backend_gates.py`` binding must turn the gate red. The
break-once proofs mutate the inputs in-process; the tree test runs the
script end-to-end and demands exit 0.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from corpus_kb.config import get_default_config
from scripts.duck_live_matrix import OPS
from test_backend_operation_parity import PARITY_OPS

REPO = Path(__file__).resolve().parents[1]
GATE_SCRIPT = REPO / "scripts" / "config_backend_parity_gate.py"
TESTS_GATES = REPO / "tests" / "test_backend_gates.py"

_spec = importlib.util.spec_from_file_location("config_backend_parity_gate", GATE_SCRIPT)
assert _spec is not None and _spec.loader is not None
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


def _gate_inputs() -> dict[str, Any]:
    """The real gate inputs: rows, both leaf sets, reader texts, tests text."""
    rows = gate._load_rows()
    packaged = yaml.safe_load((REPO / "src" / "corpus_kb" / "config.yaml").read_text(encoding="utf-8"))
    readers = sorted({row.reader for row in rows})
    texts = {}
    for reader in readers:
        path = REPO / "src" / "corpus_kb" / reader
        texts[reader] = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    return {
        "rows": rows,
        "defaults": gate.collect_leaves(get_default_config()),
        "packaged": gate.collect_leaves(packaged),
        "reader_texts": texts,
        "tests_text": TESTS_GATES.read_text(encoding="utf-8", errors="replace"),
    }


def test_every_default_leaf_declared() -> None:
    """TDD heart: the real defaults and packaged yaml are fully declared."""
    inputs = _gate_inputs()
    fails = gate.violations(
        inputs["rows"], inputs["defaults"], inputs["packaged"],
        inputs["reader_texts"], inputs["tests_text"],
    )
    assert fails == [], f"gate violations on the current tree: {fails[:5]}"


def test_undeclared_key_fails() -> None:
    """Both directions: a synthetic default leaf is red, a stale row is red."""
    inputs = _gate_inputs()
    fails = gate.violations(
        inputs["rows"], inputs["defaults"] | {"fake.new.leaf"}, inputs["packaged"],
        inputs["reader_texts"], inputs["tests_text"],
    )
    assert any("fake.new.leaf" in fail and "get_default_config" in fail for fail in fails)
    stale = [type(inputs["rows"][0])(**{**inputs["rows"][0]._asdict(), "key": "gone.yesterday"})]
    fails = gate.violations(
        stale, inputs["defaults"], inputs["packaged"], {}, inputs["tests_text"],
    )
    assert any("gone.yesterday" in fail for fail in fails)


def test_reader_missing_key_fails() -> None:
    """A row whose reader lacks the grep token (dead key) is red."""
    inputs = _gate_inputs()
    row = type(inputs["rows"][0])(**{**inputs["rows"][0]._asdict(), "token": "not-a-real-token"})
    fails = gate.violations(
        [row], inputs["defaults"], inputs["packaged"], inputs["reader_texts"],
        inputs["tests_text"],
    )
    assert any("not-a-real-token" in fail for fail in fails)


def test_refusal_row_binds_to_test() -> None:
    """Removing the hnsw binding from the backend-gates tests turns it red."""
    inputs = _gate_inputs()
    text = inputs["tests_text"].replace("HNSW_GATE_KEYS", "GONE_FROM_TESTS")
    fails = gate.violations(
        inputs["rows"], inputs["defaults"], inputs["packaged"], inputs["reader_texts"], text,
    )
    assert any("retrieval.hnsw." in fail and "bind" in fail for fail in fails)


def test_gate_green_on_current_tree() -> None:
    """The script itself exits 0 on the current tree."""
    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT)],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_ops_matrix_covers_parity_catalogue() -> None:
    """OPS_DELTA_ROWS equals PARITY_OPS plus the HTTP-only upload op."""
    from corpus_kb.ops_backend_matrix import declared_ops

    expected = set(PARITY_OPS) | {"ingest_upload"}
    assert set(declared_ops()) == expected, f"op drift: {sorted(expected ^ set(declared_ops()))}"
    assert len(declared_ops()) == len(set(declared_ops())), "the op matrix lists an op twice"
    assert set(OPS) == expected, "duck_live_matrix drift (see test_live_matrix_lists_all_parity_ops)"
