"""The ranx bridge: the one lazy import point (Wave 7 Phase 4).

ranx is the optional ``calibrate`` extra; CI never installs it, so a
direct ``import ranx`` cannot even type-check there. The module object
comes through ``importlib.import_module`` behind a Protocol cast — the
house pattern from ``eval/deepeval_adapter.py``. Absence is not an
error: ``ranx_available()`` answers the probe and the caller falls back
to the plain sign test in ``eval/stats.py``, naming the method that ran.
Every ranx call lives in THIS module so the dependency stays isolated
behind one seam (the plan's V7 rule).
"""

from __future__ import annotations

import importlib
from typing import Any, Protocol, cast

from corpus_kb.errors import ValidationError

__all__ = ["fisher_comparison", "ranx_available"]


class _RanxModule(Protocol):
    """The three ranx names the fisher comparison touches."""

    def Qrels(self) -> Any: ...  # noqa: N802 - mirrors the real library's name

    def Run(self, name: str) -> Any: ...  # noqa: N802 - mirrors the real library's name

    def compare(self, qrels: Any, runs: list[Any], metrics: list[str], stat_test: str) -> Any: ...


def _import_module(name: str) -> object:
    """The one import seam (tests substitute a ModuleNotFoundError here)."""
    return importlib.import_module(name)


def ranx_available() -> bool:
    """Whether the optional ranx extra is importable right now."""
    try:
        _import_module("ranx")
    except ImportError:
        return False
    return True


def _ranx_module() -> _RanxModule:
    """ranx through the seam, typed by the Protocol (absence already gated)."""
    return cast(_RanxModule, _import_module("ranx"))


def fisher_comparison(
    runs: dict[str, list[tuple[frozenset[str], tuple[str, ...]]]],
    k: int,
) -> dict[str, Any] | None:
    """Paired fisher randomization test across candidates via ranx compare.

    ``runs`` maps candidate name -> per-query ``(relevant, ranked)`` pairs
    in one shared query order. Every candidate must resolve the SAME
    relevant set per query — the qrels come from the first candidate, so a
    mismatch would silently score the others against foreign labels.
    Returns the ranx Report dict (per-model scores, pairwise p-values,
    win/tie/loss), or None when the extra is missing. Ranx errors other
    than absence propagate: a bug is a bug, not a fallback.
    """
    if not ranx_available():
        return None
    ranx = _ranx_module()
    first = next(iter(runs.values()))
    for name, pairs in runs.items():
        if len(pairs) != len(first):
            raise ValidationError(
                f"Candidate {name!r} scored {len(pairs)} queries; expected {len(first)}.",
                remediation="Score every candidate over the same golden queries.",
            )
        for position, (own, other) in enumerate(zip(first, pairs, strict=True), start=1):
            if own[0] != other[0]:
                raise ValidationError(
                    f"Golden labels differ across candidates at query q{position}:"
                    f" {name!r} resolved a different relevant set.",
                    remediation=(
                        "Score every candidate from the same golden file and the same label rules."
                    ),
                )
    query_ids = [f"q{index}" for index in range(len(first))]
    qrels = ranx.Qrels()
    qrels.add_multi(
        q_ids=query_ids,
        doc_ids=[sorted(relevant) for relevant, _ in first],
        scores=[[1] * len(relevant) for relevant, _ in first],
    )
    model_runs: list[Any] = []
    for name, pairs in runs.items():
        run = ranx.Run(name=name)
        run.add_multi(
            q_ids=query_ids,
            doc_ids=[list(ranked) for _, ranked in pairs],
            scores=[
                [float(len(ranked) - position) for position in range(len(ranked))]
                for _, ranked in pairs
            ],
        )
        model_runs.append(run)
    report = ranx.compare(
        qrels,
        model_runs,
        metrics=[f"recall@{k}", f"ndcg@{k}", "mrr"],
        stat_test="fisher",
    )
    return dict(report.to_dict())
