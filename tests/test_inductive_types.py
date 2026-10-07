"""Offline tests for the inductive optional-dep shim + promote gate (todo 15).

The r8 contract: WITHOUT the extra the shim imports cleanly, every access
path raises InductiveDependencyError with the install hint, and the engine
code reaches HDBSCAN's ``relative_validity_`` as a TYPED attribute (declared
on HdbscanModelLike) — no getattr/Any. Pyright enforces the typed access in
``coding/inductive_cluster.py`` (extras-less CI); the runtime tests here pin
the Protocol surface and the degraded behavior.
"""

from __future__ import annotations

import typing

import pytest

from corpus_kb.coding._inductive_types import (
    HdbscanModelLike,
    InductiveDependencyError,
    UmapModelLike,
)
from corpus_kb.coding.inductive_cluster import cluster_embeddings
from corpus_kb.research.promote_code import (
    TAU_DUP_CEIL,
    TAU_DUP_FLOOR,
    calibrate_tau_dup,
    duplicate_gate,
)


def test_shim_import_is_safe_without_extra():
    import corpus_kb.coding._inductive_types as shim

    assert callable(shim.import_umap)
    assert callable(shim.import_hdbscan)


def _extras_absent() -> bool:
    import importlib

    try:
        importlib.import_module("hdbscan")
    except ModuleNotFoundError:
        return True
    return False


@pytest.mark.skipif(not _extras_absent(), reason="inductive extra installed")
def test_cluster_entry_point_raises_with_install_hint():
    with pytest.raises(InductiveDependencyError) as excinfo:
        cluster_embeddings([[1.0, 0.0], [0.0, 1.0]])
    assert '[pip install -e ".[inductive]"]' in str(excinfo.value) or (
        "[inductive]" in str(excinfo.value)
    )


def test_hdbscan_protocol_declares_relative_validity_as_float():
    # r8/r11: the Protocol DECLARES every member used — annotations, not Any.
    annotations = typing.get_type_hints(HdbscanModelLike)
    assert "relative_validity_" in annotations
    assert annotations["relative_validity_"] is float
    assert "labels_" in annotations
    assert "probabilities_" in annotations


def test_umap_protocol_declares_determinism_surface():
    annotations = typing.get_type_hints(UmapModelLike)
    assert "embedding_" in annotations
    init = typing.get_type_hints(UmapModelLike.fit)
    assert init["X"] is not None


def test_runtime_checkable_protocols_accept_fakes():
    class FakeHdbscan:
        def __init__(self) -> None:
            self.labels_ = [0, -1]
            self.probabilities_ = [0.9, 0.1]
            self.relative_validity_ = 0.42

        def fit(self, X: object) -> FakeHdbscan:  # noqa: N803 (sklearn API)
            return self

    fake: HdbscanModelLike = FakeHdbscan()
    assert fake.relative_validity_ == 0.42  # typed attribute read; no getattr


def test_calibrate_tau_dup_from_gold_geometry():
    near = {"a": [[1.0, 0.0]], "b": [[0.999, 0.044]]}
    tau = calibrate_tau_dup(near)
    assert tau is not None
    assert tau == pytest.approx(TAU_DUP_CEIL)  # 0.999 + 0.05 clamps at the ceiling
    far = {"a": [[1.0, 0.0]], "b": [[0.0, 1.0]]}
    tau_far = calibrate_tau_dup(far)
    assert tau_far is not None
    assert tau_far == pytest.approx(TAU_DUP_FLOOR)  # 0.0 + 0.05 clamps at the floor


def test_calibrate_tau_dup_needs_two_codes():
    assert calibrate_tau_dup({}) is None
    assert calibrate_tau_dup({"a": [[1.0, 0.0]]}) is None


def test_duplicate_gate_blocks_above_tau_and_suggests_merge():
    derived = [[1.0, 0.0]]
    existing = {"trust": [[0.0, 1.0]], "trust_again": [[0.999, 0.044]]}
    blocked, suggestion, max_cos = duplicate_gate(derived, existing, tau_dup=0.85)
    assert blocked is True
    assert suggestion == "trust_again"
    assert max_cos == pytest.approx(0.999, abs=1e-3)

    blocked2, suggestion2, max_cos2 = duplicate_gate(derived, {"trust": [[0.0, 1.0]]}, 0.85)
    assert blocked2 is False
    assert suggestion2 is None
    assert max_cos2 == pytest.approx(0.0, abs=1e-6)


def test_duplicate_gate_empty_existing_never_blocks():
    blocked, suggestion, _ = duplicate_gate([[1.0, 0.0]], {}, tau_dup=0.5)
    assert blocked is False
    assert suggestion is None
