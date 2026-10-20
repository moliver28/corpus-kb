"""Tier B: the opt-in SetFit tier (WP-3, ``[setfit]`` extra).

Frozen body first (the library's own default): ``SetFitModel.from_pretrained``
loads bge-small-en-v1.5 — a 512-token encoder, so our 512-token chunks
survive intact where MiniLM's 256-token window would truncate (F4) — and
``fit(end_to_end=False)`` trains only the head. The body never moves.

LAZY DEPENDENCY: setfit and torch are imported inside functions, so the
extras-less runtime (and CI) never touches them. A missing extra raises
the typed ``SetfitDependencyError`` (HTTP 503 / socket -32005 / CLI exit 3).
:func:`load_setfit` is the module-level seam — tests substitute a fake
module there, so no test ever downloads a model.

DETERMINISM (F1): the torch seed is pinned at 42 and deterministic
algorithms are requested; a backend that refuses the request is recorded
in ``params`` (``deterministic_mode``), never silently ignored. GPU is a
bulk-inference concern only; training stays on CPU.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from types import ModuleType
from typing import Protocol, cast

import numpy as np

from corpus_kb.errors import DependencyMissingError

from .tier_a import sklearn_version, thread_pin

__all__ = [
    "SETFIT_MODEL_ID",
    "TORCH_SEED",
    "SetfitDependencyError",
    "SetfitFit",
    "fit_setfit",
    "load_setfit",
]

SETFIT_MODEL_ID = "BAAI/bge-small-en-v1.5"
SETFIT_EXTRA_REMEDIATION = "pip install 'corpus-kb[setfit]'"
TORCH_SEED = 42
DEVICE = "cpu"
SAMPLE_POLICY = "seed_rows_only"


class SetfitDependencyError(DependencyMissingError):
    """The ``[setfit]`` extra is not installed."""

    remediation = SETFIT_EXTRA_REMEDIATION


class _SetfitBody(Protocol):
    """The encoder surface the tier touches (semi-public, stable)."""

    def encode(self, sentences: list[str]) -> object: ...


class _SetfitHead(Protocol):
    """The sklearn head surface (decision scores drive calibration)."""

    def decision_function(self, x: object) -> object: ...


class _SetfitModel(Protocol):
    """The loaded SetFitModel surface this tier uses."""

    model_body: _SetfitBody
    model_head: _SetfitHead

    def fit(
        self, x_train: list[str], y_train: list[str], *, end_to_end: bool = False
    ) -> object: ...


class _SetfitFactory(Protocol):
    """``SetFitModel`` as the module exposes it (a classmethod entry)."""

    def from_pretrained(self, model_id: str, *, device: str) -> _SetfitModel: ...


class _TorchModule(Protocol):
    """The torch surface this tier pins."""

    def manual_seed(self, seed: int) -> None: ...

    def use_deterministic_algorithms(self, flag: bool) -> None: ...


def _import_module(name: str) -> ModuleType:
    """The one import seam (tests substitute a ModuleNotFoundError here)."""
    return importlib.import_module(name)


def load_setfit() -> ModuleType:
    """Import setfit lazily; a missing extra is a typed error."""
    try:
        return _import_module("setfit")
    except ModuleNotFoundError as exc:
        raise SetfitDependencyError(
            "The setfit tier needs the setfit package (the [setfit] extra)."
        ) from exc


def _load_torch() -> ModuleType:
    """Import torch lazily (setfit pulls it; keep the error typed anyway)."""
    try:
        return _import_module("torch")
    except ModuleNotFoundError as exc:
        raise SetfitDependencyError(
            "The setfit tier needs torch (pulled in by the [setfit] extra)."
        ) from exc


@dataclass(frozen=True)
class SetfitFit:
    """A fitted tier-B model: embed, score, and the params record."""

    model: _SetfitModel
    classes: list[str]
    params: dict[str, object] = field(default_factory=dict)

    def embed(self, texts: list[str]) -> np.ndarray:
        """Body embeddings (float32) for one ordered list of texts."""
        raw = self.model.model_body.encode(list(texts))
        return np.asarray(raw, dtype=np.float32)

    def decision_scores(self, features: np.ndarray) -> np.ndarray:
        """Head decision scores over embedding feature rows (single-thread pinned)."""
        with thread_pin():
            return np.asarray(self.model.model_head.decision_function(features))


def fit_setfit(train_texts: list[str], train_labels: list[str]) -> SetfitFit:
    """Load, pin seeds, fit the head (frozen body); record everything.

    Raises ``SetfitDependencyError`` when the ``[setfit]`` extra is absent.
    """
    module = load_setfit()
    torch = cast(_TorchModule, _load_torch())
    params: dict[str, object] = {
        "tier": "setfit",
        "model": SETFIT_MODEL_ID,
        "end_to_end": False,
        "device": DEVICE,
        "torch_seed": TORCH_SEED,
        "sklearn_version": sklearn_version(),
        "n_train": len(train_labels),
        "classes": sorted(set(train_labels)),
        "sample_policy": SAMPLE_POLICY,
    }
    _pin_torch(torch, params)
    params["setfit_version"] = str(getattr(module, "__version__", "unknown"))
    params["torch_version"] = str(getattr(torch, "__version__", "unknown"))
    factory = cast(_SetfitFactory, module.SetFitModel)
    model = factory.from_pretrained(SETFIT_MODEL_ID, device=DEVICE)
    model.fit(list(train_texts), list(train_labels), end_to_end=False)
    return SetfitFit(model=model, classes=sorted(set(train_labels)), params=params)


def _pin_torch(torch: _TorchModule, params: dict[str, object]) -> None:
    """Seed the RNG and request deterministic algorithms (F1).

    A backend that refuses the deterministic request is a recorded note,
    not a crash: the honest-params convention beats a hard stop here.
    """
    torch.manual_seed(TORCH_SEED)
    try:
        torch.use_deterministic_algorithms(True)
    except Exception as exc:
        params["deterministic_mode"] = f"unavailable: {exc}"
        return
    params["deterministic_mode"] = "enforced"
