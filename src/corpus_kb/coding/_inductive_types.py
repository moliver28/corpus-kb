"""Typed Protocol wrappers for the optional inductive dependency stack.

House pattern (``extraction/_langextract_types.py``,
``rag/embedders/_latechunk_types.py``): NO bare ``import umap`` / ``import
hdbscan`` anywhere in the package — a bare import, even guarded by
try/except, trips pyright ``reportMissingImports=error`` in the extras-less
CI type-check job (r8). Every access goes through ``importlib.import_module``
+ ``cast`` onto the Protocols below, which declare exactly the members we
use — including HDBSCAN's ``relative_validity_: float`` (r8/r11: DBCV access
stays pyright-clean, no getattr/Any escape hatches).

Importing this module is SAFE without the extra: nothing heavy loads until
one of the ``import_*`` functions runs (and those raise
:class:`InductiveDependencyError` with an install hint when absent).
"""

from __future__ import annotations

import importlib
from typing import Protocol, cast, runtime_checkable

import numpy as np


class InductiveDependencyError(RuntimeError):
    """Raised when the ``inductive`` optional extra is not installed."""


_INSTALL_HINT = (
    "the inductive extra is not installed — run "
    '`pip install -e ".[inductive]"` (umap-learn + hdbscan)'
)


def import_or_raise(module: str) -> object:
    """importlib.import_module with the house install hint on failure."""
    try:
        return importlib.import_module(module)
    except ModuleNotFoundError as exc:
        raise InductiveDependencyError(_INSTALL_HINT) from exc


@runtime_checkable
class UmapModelLike(Protocol):
    """Fitted UMAP model surface used by the inductive engine.

    Determinism pins (r7) travel through the constructor — random_state and
    transform_seed are REQUIRED keyword arguments at every construction site
    and are recorded in the run manifest.
    """

    embedding_: np.ndarray

    def fit(self, X: np.ndarray) -> UmapModelLike: ...  # noqa: N803 (sklearn API)

    def transform(self, X: np.ndarray) -> np.ndarray: ...  # noqa: N803


class UmapModuleLike(Protocol):
    """``umap`` module surface used by the inductive engine."""

    def UMAP(  # noqa: N802
        self,
        *,
        n_neighbors: int = ...,
        n_components: int = ...,
        metric: str = ...,
        random_state: int = ...,
        transform_seed: int = ...,
    ) -> UmapModelLike: ...


@runtime_checkable
class HdbscanModelLike(Protocol):
    """Fitted HDBSCAN model surface used by the inductive engine.

    ``relative_validity_`` is the DBCV index (Moulavi et al. 2014) — the
    density-appropriate cluster-validity score tracked across checkpoints
    (r10). Declared here so engine access is a typed attribute read, never
    getattr/Any.
    """

    labels_: np.ndarray
    probabilities_: np.ndarray
    relative_validity_: float

    def fit(self, X: np.ndarray) -> HdbscanModelLike: ...  # noqa: N803


class HdbscanModuleLike(Protocol):
    """``hdbscan`` module surface used by the inductive engine."""

    def HDBSCAN(  # noqa: N802
        self,
        *,
        min_cluster_size: int = ...,
        min_samples: int | None = ...,
        metric: str = ...,
        cluster_selection_method: str = ...,
    ) -> HdbscanModelLike: ...


def import_umap() -> UmapModuleLike:
    """Lazily import ``umap`` and cast to the typed Protocol."""
    return cast(UmapModuleLike, import_or_raise("umap"))


def import_hdbscan() -> HdbscanModuleLike:
    """Lazily import ``hdbscan`` and cast to the typed Protocol."""
    return cast(HdbscanModuleLike, import_or_raise("hdbscan"))
