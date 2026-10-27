"""The ranx bridge: the one lazy import point (Wave 7 Phase 4).

ranx is the optional ``calibrate`` extra; CI never installs it. Absence is
not an error: ``ranx_available()`` answers the probe and the caller falls
back to the plain sign test in ``eval/stats.py``, naming the method that
ran. Every ranx call lives in THIS module so the dependency stays isolated
behind one seam (the plan's V7 rule).
"""

from __future__ import annotations

from typing import Any

__all__ = ["fisher_comparison", "ranx_available"]


def ranx_available() -> bool:
    """Whether the optional ranx extra is importable right now."""
    try:
        import ranx  # noqa: F401
    except ImportError:
        return False
    return True


def fisher_comparison(
    runs: dict[str, list[tuple[frozenset[str], tuple[str, ...]]]],
    k: int,
) -> dict[str, Any] | None:
    """Paired fisher randomization test across candidates via ranx compare.

    ``runs`` maps candidate name -> per-query ``(relevant, ranked)`` pairs
    in one shared query order. Returns the ranx Report dict (per-model
    scores, pairwise p-values, win/tie/loss), or None when the extra is
    missing. Ranx errors other than absence propagate: a bug is a bug,
    not a fallback.
    """
    if not ranx_available():
        return None
    from ranx import Qrels, Run, compare

    first = next(iter(runs.values()))
    query_ids = [f"q{index}" for index in range(len(first))]
    qrels = Qrels()
    qrels.add_multi(
        q_ids=query_ids,
        doc_ids=[sorted(relevant) for relevant, _ in first],
        scores=[[1] * len(relevant) for relevant, _ in first],
    )
    model_runs: list[Run] = []
    for name, pairs in runs.items():
        run = Run(name=name)
        run.add_multi(
            q_ids=query_ids,
            doc_ids=[list(ranked) for _, ranked in pairs],
            scores=[
                [float(len(ranked) - position) for position in range(len(ranked))]
                for _, ranked in pairs
            ],
        )
        model_runs.append(run)
    report = compare(
        qrels,
        model_runs,
        metrics=[f"recall@{k}", f"ndcg@{k}", "mrr"],
        stat_test="fisher",
    )
    return dict(report.to_dict())
