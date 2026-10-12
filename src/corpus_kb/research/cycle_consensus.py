"""U48 N-run inductive consensus, wired to the live ``research.consensus.runs``.

``research.consensus.runs`` (default 3) makes the cycle's inductive stage an
N-run protocol: N INDEPENDENT inductive passes run, then every pass's
proposed codes are aggregated through
``research/release/consensus.aggregate_runs`` — deterministic verbatim-quote
gating plus greedy embedding clustering — before any theme is surfaced as
consensus-backed. ``runs <= 1`` keeps the single-run path unchanged.

Settings are read fail-loud from config (this module is the reader that made
the pre-staged key live):

  * ``research.consensus.runs``                — N independent passes;
  * ``research.consensus.min_run_fraction``    — theme kept when seen in >=
    this fraction of the runs;
  * ``research.consensus.similarity_threshold`` — cosine at which candidates
    join the same consensus theme.

Candidate evidence quotes are the member unit's OWN transcript text (the
first member unit), so the deterministic quote gate verifies text that is
verbatim by construction — no quote is invented.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.research.release.consensus import Candidate, ConsensusResult, aggregate_runs

CONSENSUS_CONFIG_DEFAULTS: dict[str, object] = {
    "runs": 3,
    "min_run_fraction": 0.5,
    "similarity_threshold": 0.8,
}

PROPOSALS_SQL = """
SELECT p.run_id::text AS run_id, p.cluster_id, p.suggested_label,
       p.centroid, p.member_unit_ids, u.text AS first_member_text
FROM research_proposed_codes p
LEFT JOIN research_units u
  ON u.tenant_id = p.tenant_id AND u.unit_id = p.member_unit_ids[1]
WHERE p.tenant_id = $1 AND p.run_id = ANY($2::uuid[]) AND p.status = 'proposed'
ORDER BY p.run_id, p.proposed_id
"""


@dataclass(frozen=True)
class ConsensusSettings:
    """Typed ``research.consensus.*`` reads (config is the source)."""

    runs: int = 3
    min_run_fraction: float = 0.5
    similarity_threshold: float = 0.8


def consensus_settings(config: dict[str, Any]) -> ConsensusSettings:
    """Read ``research.consensus.*`` fail-loud (types and ranges checked)."""
    research = config.get("research")
    if research is not None and not isinstance(research, dict):
        raise ValueError("config.research must be a mapping")
    block = (research or {}).get("consensus") or {}
    if not isinstance(block, dict):
        raise ValueError("research.consensus must be a mapping")
    runs_raw = block.get("runs", CONSENSUS_CONFIG_DEFAULTS["runs"])
    if isinstance(runs_raw, bool) or not isinstance(runs_raw, int) or runs_raw < 1:
        raise ValueError(f"research.consensus.runs must be a positive integer, got {runs_raw!r}")
    fraction_raw = block.get("min_run_fraction", CONSENSUS_CONFIG_DEFAULTS["min_run_fraction"])
    if (
        isinstance(fraction_raw, bool)
        or not isinstance(fraction_raw, (int, float))
        or not 0.0 <= float(fraction_raw) <= 1.0
    ):
        raise ValueError(
            f"research.consensus.min_run_fraction must be within [0, 1], got {fraction_raw!r}"
        )
    threshold_raw = block.get(
        "similarity_threshold", CONSENSUS_CONFIG_DEFAULTS["similarity_threshold"]
    )
    if (
        isinstance(threshold_raw, bool)
        or not isinstance(threshold_raw, (int, float))
        or not 0.0 <= float(threshold_raw) <= 1.0
    ):
        raise ValueError(
            f"research.consensus.similarity_threshold must be within [0, 1], got {threshold_raw!r}"
        )
    return ConsensusSettings(
        runs=int(runs_raw),
        min_run_fraction=float(fraction_raw),
        similarity_threshold=float(threshold_raw),
    )


@dataclass(frozen=True)
class ProposalSeed:
    """One proposed code row joined with its first member unit's text."""

    run_id: str
    cluster_id: str
    label: str
    centroid: list[float]
    first_member_text: str

    def to_candidate(self) -> Candidate:
        """The consensus Candidate; the quote IS the member unit's text."""
        return Candidate(
            run_id=self.run_id,
            label=self.label or self.cluster_id,
            definition=self.label or self.cluster_id,
            embedding=self.centroid,
            quote=self.first_member_text,
            unit_id=0,
            unit_text=self.first_member_text,
            candidate_id=self.cluster_id,
        )


async def load_proposal_seeds(
    pool: asyncpg.Pool, tenant_id: UUID, run_ids: list[str]
) -> list[ProposalSeed]:
    """Load the proposed codes of the given inductive runs (tenant-scoped)."""
    from corpus_kb.projections.research._embed import _parse_pg_vector
    from corpus_kb.storage.tenant_conn import tenant_connection

    if not run_ids:
        return []
    async with tenant_connection(pool, tenant_id) as conn:
        rows = await conn.fetch(PROPOSALS_SQL, UUID(str(tenant_id)), run_ids)
    seeds: list[ProposalSeed] = []
    for row in rows:
        centroid = row["centroid"]
        if centroid is None:
            continue  # a proposal without a stored centroid cannot vote
        text = row["first_member_text"]
        seeds.append(
            ProposalSeed(
                run_id=str(row["run_id"]),
                cluster_id=str(row["cluster_id"]),
                label=str(row["suggested_label"] or ""),
                centroid=[float(x) for x in _parse_pg_vector(centroid)],
                first_member_text=str(text) if text is not None else "",
            )
        )
    return seeds


def consensus_from_seeds(
    seeds: list[ProposalSeed], settings: ConsensusSettings, run_ids: list[str]
) -> ConsensusResult:
    """Group the proposals by run and aggregate (U48 multi-run consensus).

    ADVERSARIAL REVIEW F6: the run fraction is over ALL executed runs —
    proposal-less runs feed EMPTY candidate lists, so a theme seen in one
    of two proposal-bearing runs out of three executed can never clear
    ``min_run_fraction: 0.5`` (1/3, not 1/1).
    """
    by_run: dict[str, list[Candidate]] = {}
    for seed in seeds:
        by_run.setdefault(seed.run_id, []).append(seed.to_candidate())
    ordered = [by_run.get(run_id, []) for run_id in run_ids]
    return (
        aggregate_runs(
            ordered,
            min_run_fraction=settings.min_run_fraction,
            similarity_threshold=settings.similarity_threshold,
        )
        if ordered
        else _empty_consensus(settings, max(1, len(run_ids)))
    )


def _empty_consensus(settings: ConsensusSettings, n_runs: int) -> ConsensusResult:
    """Honest empty result: no proposals in any run, nothing invented."""
    return ConsensusResult(
        n_runs=n_runs,
        themes_kept=0,
        min_run_fraction=settings.min_run_fraction,
        similarity_threshold=settings.similarity_threshold,
        quote_not_found_rejected=0,
        kept=(),
        rejected=(),
        run_to_run_agreement={},
        note="no proposed codes in any run; nothing to aggregate",
    )


async def run_inductive_consensus(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    cfg: dict[str, Any],
    project_id: UUID | None,
    run_once: Any,
) -> dict[str, object] | None:
    """Run the inductive stage N times and aggregate the proposals (U48).

    ``run_once`` is the cycle's single-pass inductive stage runner (injected
    so this stays callable offline); each call is one INDEPENDENT inductive
    run with its own run aggregate. Returns the consensus block for the
    stage receipt, or None when ``research.consensus.runs <= 1`` (the
    single-run path is unchanged).
    """
    settings = consensus_settings(cfg)
    if settings.runs <= 1:
        return None
    receipts: list[dict[str, Any]] = []
    for _ in range(settings.runs):
        receipts.append(dict(await run_once()))
    run_ids = [
        str(receipt["run_id"])
        for receipt in receipts
        if receipt.get("status") == "success" and receipt.get("run_id")
    ]
    if not run_ids:
        # Every pass failed: surface the last stage receipt verbatim so the
        # normal unavailable path (and its reason) is what the cycle reports.
        return receipts[-1] if receipts else None
    seeds = await load_proposal_seeds(pool, tenant_id, run_ids)
    result = consensus_from_seeds(seeds, settings, run_ids)
    last = receipts[-1]
    return {
        "status": "success",
        # Gate reads anchor on run_id (kept working) and span run_ids (the
        # N-run proposal log); drift fields merge across passes so an alarm
        # in ANY pass still fires (never papered by aggregation).
        "run_id": run_ids[-1],
        "run_ids": run_ids,
        "dbcv_drop_flag": any(bool(r.get("dbcv_drop_flag")) for r in receipts),
        "dbcv_relative_validity": last.get("dbcv_relative_validity"),
        "dbcv_baseline": last.get("dbcv_baseline"),
        "runs_requested": settings.runs,
        "runs_completed": len(run_ids),
        "consensus": result.to_dict(),
    }
