"""U48 multi-run consensus + verbatim-quote gating over inductive candidates.

(1) Every candidate must carry verbatim supporting quotes; they are verified
    DETERMINISTICALLY with ``coding/quote_verification.classify_quote``
    (imported, never duplicated). A candidate whose quote is ``not_found``
    is REJECTED with reason ``quote_not_found`` — before it can vote in the
    consensus.
(2) N runs of candidate generation (config default 3) are aggregated:
    candidates cluster by embedding-similarity (greedy leader clustering,
    cosine >= ``similarity_threshold``); a theme is KEPT when it appears in
    >= ``min_run_fraction`` of the runs. Per-theme consistency (mean pairwise
    similarity), similarity stats, and run-to-run agreement (ARI over the
    consensus-theme label space on each run pair's shared candidates) are
    recorded — never papered numbers.
(3) Reviewer rubric verdicts (agreement | reasonable_alternative |
    not_reasonable) are aggregate events (``ReviewerRubricRecorded`` on the
    CodebookVersion aggregate); Wave 2 wires ``review_surface.py`` to them.

Thresholds are ADMIN-CONFIGURED (passed in, no silent defaults) — published
LLM-human kappas vary too widely to hardcode policy.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace

import numpy as np

from corpus_kb.coding.quote_verification import classify_quote
from corpus_kb.research.cluster_stability import ari

QUOTE_NOT_FOUND = "quote_not_found"
DEFAULT_N_RUNS = 3


@dataclass(frozen=True)
class Candidate:
    """One proposed code from one run, with its claimed evidence."""

    run_id: str
    label: str
    definition: str
    embedding: Sequence[float]
    quote: str
    unit_id: int
    unit_text: str
    candidate_id: str = ""

    def key(self) -> str:
        return self.candidate_id or f"{self.run_id}:{self.label}"


@dataclass(frozen=True)
class Theme:
    """A consensus cluster: members, supporting runs, consistency stats."""

    members: tuple[Candidate, ...]
    supporting_runs: frozenset[str]
    mean_similarity: float
    min_similarity: float

    def representative(self) -> Candidate:
        """Highest-definition member (longest definition breaks ties by order)."""
        return max(self.members, key=lambda c: len(c.definition))


@dataclass(frozen=True)
class RejectedCandidate:
    """A candidate that failed verbatim-quote verification."""

    candidate: Candidate
    reason: str = QUOTE_NOT_FOUND


@dataclass(frozen=True)
class ConsensusResult:
    """The evidence block the release gate consumes."""

    n_runs: int
    themes_kept: int
    min_run_fraction: float
    similarity_threshold: float
    quote_not_found_rejected: int
    kept: tuple[Theme, ...] = field(default_factory=tuple)
    rejected: tuple[RejectedCandidate, ...] = field(default_factory=tuple)
    run_to_run_agreement: dict[str, float] = field(default_factory=dict)
    note: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "n_runs": self.n_runs,
            "themes_kept": self.themes_kept,
            "min_run_fraction": self.min_run_fraction,
            "similarity_threshold": self.similarity_threshold,
            "quote_not_found_rejected": self.quote_not_found_rejected,
            "themes": [
                {
                    "label": t.representative().label,
                    "definition": t.representative().definition,
                    "supporting_runs": sorted(t.supporting_runs),
                    "n_members": len(t.members),
                    "mean_similarity": t.mean_similarity,
                    "min_similarity": t.min_similarity,
                }
                for t in self.kept
            ],
            "rejected": [
                {"candidate": r.candidate.key(), "reason": r.reason} for r in self.rejected
            ],
            "run_to_run_agreement": dict(self.run_to_run_agreement),
            "note": self.note,
        }


def _unit(vector: Sequence[float]) -> np.ndarray:
    arr = np.asarray(vector, dtype=np.float64)
    norm = float(np.linalg.norm(arr))
    return arr / norm if norm > 0 else arr


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    return float(np.clip(np.dot(_unit(a), _unit(b)), -1.0, 1.0))


def verify_quotes(
    candidates: Sequence[Candidate],
) -> tuple[list[Candidate], list[RejectedCandidate]]:
    """Deterministic quote gate: exact / normalized_match pass; not_found rejects."""
    ok: list[Candidate] = []
    rejected: list[RejectedCandidate] = []
    for candidate in candidates:
        status, _repaired = classify_quote(candidate.quote, candidate.unit_text)
        if status == "not_found":
            rejected.append(RejectedCandidate(candidate=candidate))
        else:
            ok.append(candidate)
    return ok, rejected


def build_themes(
    candidates: Sequence[Candidate],
    similarity_threshold: float,
) -> list[Theme]:
    """Greedy leader clustering: join the first theme whose centroid clears
    the threshold, else open a new theme. Centroids re-average on join."""
    leaders: list[np.ndarray] = []
    buckets: list[list[Candidate]] = []
    for candidate in candidates:
        vec = _unit(candidate.embedding)
        placed = False
        for i, leader in enumerate(leaders):
            if float(np.dot(vec, leader)) >= similarity_threshold:
                buckets[i].append(candidate)
                members = np.asarray([_unit(m.embedding) for m in buckets[i]])
                leaders[i] = members.mean(axis=0)
                placed = True
                break
        if not placed:
            leaders.append(vec)
            buckets.append([candidate])
    themes: list[Theme] = []
    for bucket in buckets:
        sims = [
            _cosine(a.embedding, b.embedding) for i, a in enumerate(bucket) for b in bucket[i + 1 :]
        ]
        themes.append(
            Theme(
                members=tuple(bucket),
                supporting_runs=frozenset(m.run_id for m in bucket),
                mean_similarity=float(np.mean(sims)) if sims else 1.0,
                min_similarity=float(np.min(sims)) if sims else 1.0,
            )
        )
    return themes


def run_to_run_agreement(
    kept: Sequence[Theme],
    run_labels: Sequence[str],
) -> dict[str, float]:
    """Pairwise ARI over the consensus-theme label space on shared candidates.

    ``run_labels`` are the candidates' ACTUAL run_id values (verified against
    the candidate keys, not assumed).
    """
    theme_of: dict[str, int] = {}
    for idx, theme in enumerate(kept):
        for member in theme.members:
            theme_of[member.key()] = idx
    keys_by_run: dict[str, set[str]] = {label: set() for label in run_labels}
    for key, _theme in theme_of.items():
        for label in run_labels:
            if key.startswith(f"{label}:"):
                keys_by_run[label].add(key)
                break
    agreement: dict[str, float] = {}
    for i, run_a in enumerate(run_labels):
        for run_b in run_labels[i + 1 :]:
            shared = sorted(keys_by_run[run_a] & keys_by_run[run_b])
            if len(shared) < 2:
                agreement[f"{run_a}|{run_b}"] = 0.0
                continue
            labels_a = [theme_of[k] for k in shared]
            labels_b = [theme_of[k] for k in shared]
            agreement[f"{run_a}|{run_b}"] = ari(labels_a, labels_b)
    return agreement


def aggregate_runs(
    runs: Sequence[Sequence[Candidate]],
    *,
    min_run_fraction: float,
    similarity_threshold: float,
) -> ConsensusResult:
    """Quote-gate, cluster, and keep themes meeting the run fraction."""
    if not 0.0 <= min_run_fraction <= 1.0:
        raise ValueError("min_run_fraction must be within [0, 1]")
    if not 0.0 <= similarity_threshold <= 1.0:
        raise ValueError("similarity_threshold must be within [0, 1]")
    run_labels: list[str] = []
    verified: list[Candidate] = []
    rejected: list[RejectedCandidate] = []
    for idx, run in enumerate(runs):
        label = run[0].run_id if run else f"run{idx}"
        run_labels.append(label)
        ok, bad = verify_quotes(
            [replace(c, candidate_id=f"{label}:{j}") for j, c in enumerate(run)]
        )
        verified.extend(ok)
        rejected.extend(bad)
    themes = build_themes(verified, similarity_threshold)
    n_runs = max(1, len(runs))
    min_support = min_run_fraction * n_runs
    kept = tuple(
        t for t in themes if len(t.supporting_runs) >= max(1, int(np.ceil(min_support - 1e-9)))
    )
    return ConsensusResult(
        n_runs=len(runs),
        themes_kept=len(kept),
        min_run_fraction=min_run_fraction,
        similarity_threshold=similarity_threshold,
        quote_not_found_rejected=len(rejected),
        kept=kept,
        rejected=tuple(rejected),
        run_to_run_agreement=run_to_run_agreement(kept, run_labels),
    )
