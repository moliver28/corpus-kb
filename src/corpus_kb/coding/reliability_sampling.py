from __future__ import annotations

import random
from typing import Any, TypeVar

T = TypeVar("T")


def _allocate_with_remainder(target_total: int, capacities: list[int]) -> list[int]:
    """Split `target_total` units across strata sized by `capacities`.

    Starts from an equal per-stratum share and uses largest-remainder
    rounding (rather than plain floor division) so the allocations sum to
    `min(target_total, sum(capacities))` instead of silently undershooting
    by up to `len(capacities) - 1` units. Any stratum too small to hold its
    share has the shortfall redistributed to strata with spare capacity, so
    the code-level total is still met whenever enough rows exist somewhere
    in the code's strata.
    """
    n = len(capacities)
    if n == 0 or target_total <= 0:
        return [0] * n

    total_capacity = sum(capacities)
    target_total = min(target_total, total_capacity)

    base = target_total / n
    floors = [int(base) for _ in range(n)]
    remainders = [base - f for f in floors]
    remaining = target_total - sum(floors)

    order = sorted(range(n), key=lambda i: remainders[i], reverse=True)
    for i in order[:remaining]:
        floors[i] += 1

    allocation = [min(floors[i], capacities[i]) for i in range(n)]
    shortfall = target_total - sum(allocation)
    while shortfall > 0:
        spare_idxs = [i for i in range(n) if allocation[i] < capacities[i]]
        if not spare_idxs:
            break
        spare_idxs.sort(key=lambda i: capacities[i] - allocation[i], reverse=True)
        for i in spare_idxs:
            if shortfall <= 0:
                break
            allocation[i] += 1
            shortfall -= 1

    return allocation


def stratified_sample(
    rows: list[dict[str, Any]], per_code_min: int, seed: int
) -> list[dict[str, Any]]:
    """
    Stratified sampling across all three dispositions per code.

    Draws rows stratified by (code_id, disposition) so that, for every code
    present in the input, the total sampled across its disposition strata
    reaches `per_code_min` (capped by however many rows actually exist for
    that code). Allocation across a code's strata uses largest-remainder
    rounding rather than plain floor division: `per_code_min // n_strata`
    silently undershoots whenever per_code_min isn't an exact multiple of
    the stratum count (e.g. per_code_min=5 over 3 dispositions floored to
    1 each, 3 sampled total instead of 5).

    Args:
        rows: List of row dicts, each with at minimum:
              {"chunk_id": str, "code_id": str, "disposition": str}
        per_code_min: Minimum total sample size per code, across its strata.
        seed: Random seed for reproducibility.

    Returns:
        List of sampled rows, stratified by (code_id, disposition).
    """
    rng = random.Random(seed)

    # Group by (code_id, disposition)
    strata: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        code_id = row["code_id"]
        disposition = row["disposition"]
        key = (code_id, disposition)
        if key not in strata:
            strata[key] = []
        strata[key].append(row)

    # Group by code_id to track which dispositions appear, in stable order
    codes_dispositions: dict[str, list[str]] = {}
    for code_id, disposition in strata:
        codes_dispositions.setdefault(code_id, []).append(disposition)

    # Sample from each code's strata, allocating per_code_min across them
    sample = []
    for code_id, dispositions in codes_dispositions.items():
        capacities = [len(strata[(code_id, d)]) for d in dispositions]
        allocation = _allocate_with_remainder(per_code_min, capacities)
        for disposition, n_to_sample in zip(dispositions, allocation, strict=False):
            rows_in_stratum = strata[(code_id, disposition)]
            sampled = rng.sample(rows_in_stratum, n_to_sample)
            sample.extend(sampled)

    return sample


def insufficient_codes(counts: dict[str, int], per_code_min: int) -> list[str]:
    """
    Identify codes with sample sizes below the per_code_min floor.

    Args:
        counts: Dict mapping code_id to count.
        per_code_min: Minimum required count.

    Returns:
        List of code_ids with count < per_code_min, sorted.
    """
    return sorted([code_id for code_id, count in counts.items() if count < per_code_min])
