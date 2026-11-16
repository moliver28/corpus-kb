"""Telemetry consistency report (issue #442): read-only confirmation.

Reads the opt-in JSONL event log and CONFIRMS the derived ladder for a
human reviewer: first-attempt success rates, an IITA-style order check
over #439's ladder edges, and a co-struggle check over the register's
teaching concepts. The script NEVER writes anything — no register rows,
no rungs, no order. It is a standalone stdlib script by decision D-1
(not a doctor section): the register and the edge derivation load
lazily inside functions, so importing this module stays PyYAML-free.

Determinism (program rule 9): files are read in sorted name order,
malformed lines are skipped with a count, every table is sorted, and
the output is capped — the same log folder yields the same bytes.

Known gaps the header states: shared ops emit ``method_id`` ``null``
and are excluded from order checks, and the socket hand-arm paths for
``search`` (23) and ``ingest_text`` (25) emit no events.

Usage: ``python scripts/telemetry_report.py [LOG_DIR]``
"""

from __future__ import annotations

import heapq
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

CAP_DEFAULT = 50
RATE_GAP_FLAG = 0.5


def consistency_report(log_dir: str, *, cap: int = CAP_DEFAULT) -> str:
    """The full report text for one log folder; byte-stable for the same input."""
    events, skipped = _read_events(Path(log_dir))
    methods = _load_methods()
    rates = _first_attempt_rates(events)
    order = _ladder_order(methods)
    violations = _order_violations(events, order)
    flags = _concept_flags(methods, rates)
    lines = [_header(log_dir, skipped)]
    lines.extend(_rate_lines(events, rates))
    lines.extend(_violation_lines(violations, cap))
    lines.extend(flags)
    return "\n".join(lines) + "\n"


def _header(log_dir: str, skipped: int) -> str:
    return "\n".join(
        [
            "Telemetry consistency report",
            f"Source log dir: {log_dir}",
            f"Malformed lines skipped: {skipped}",
            "Shared ops emit method_id null; they are excluded from order checks.",
            "The socket hand-arm paths for search (23) and ingest_text (25) emit no events.",
            "Ladder order source: the local _ladder_order adapter over derive_edges",
            "edges (issue #439; owner-confirm F3). Read-only: nothing is written.",
        ]
    )


def _read_events(base: Path) -> tuple[list[dict[str, Any]], int]:
    """All events from the sorted ``*.jsonl`` files, in observation order."""
    events: list[dict[str, Any]] = []
    skipped = 0
    if not base.is_dir():
        return events, skipped
    for path in sorted(base.glob("*.jsonl"), key=lambda item: item.name):
        for raw in path.read_text(encoding="utf-8").splitlines():
            row = _parse_line(raw)
            if row is None:
                skipped += 1
            else:
                events.append(row)
    return events, skipped


def _parse_line(raw: str) -> dict[str, Any] | None:
    """One parsed event row, or ``None`` for a malformed line."""
    line = raw.strip()
    if not line:
        return None
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(row, dict) or not isinstance(row.get("method_id"), str):
        return None
    return row


def _first_attempt_rates(events: Sequence[dict[str, Any]]) -> dict[str, tuple[int, int]]:
    """Per method: (first-attempt successes, first attempts)."""
    totals: dict[str, tuple[int, int]] = {}
    for row in events:
        if row.get("first_attempt") is not True:
            continue
        method_id = str(row["method_id"])
        successes, attempts = totals.get(method_id, (0, 0))
        hit = row.get("success") is True
        totals[method_id] = (successes + (1 if hit else 0), attempts + 1)
    return totals


def _rate_lines(
    events: Sequence[dict[str, Any]], rates: dict[str, tuple[int, int]]
) -> list[str]:
    """The first-attempt success-rate table: methods with >= 1 event, sorted."""
    seen = {str(row["method_id"]) for row in events}
    lines = ["", "First-attempt success rate (successes / first attempts):"]
    for method_id in sorted(seen):
        successes, attempts = rates.get(method_id, (0, 0))
        if attempts:
            lines.append(f"  {method_id}: {successes}/{attempts} = {successes / attempts:.2f}")
        else:
            lines.append(f"  {method_id}: no first-attempt events")
    return lines


def _load_methods() -> Sequence[Any]:
    """The register rows; the corpus_kb import stays lazy (D-1)."""
    from corpus_kb.analysis.method_register_loader import load_method_register

    return load_method_register().methods


def _ladder_order(methods: Sequence[Any]) -> list[str]:
    """Deterministic Kahn order over #439's ``derive_edges`` (owner-confirm F3).

    #439 exports edges, not an order, so this adapter derives one: the
    edges are deduped into a set, Kahn's algorithm pops the id-minimal
    zero-indegree method from a heap, and any cycle remainder (the
    register validator already rejects cycles) is appended in id order.
    """
    from corpus_kb.analysis.method_register_dataflow import derive_edges

    ids = sorted({str(record.id) for record in methods})
    dependents: dict[str, list[str]] = {method_id: [] for method_id in ids}
    indegree: dict[str, int] = {method_id: 0 for method_id in ids}
    for source, target in sorted(set(derive_edges(methods))):
        dependents[source].append(target)
        indegree[target] += 1
    heap = [method_id for method_id in ids if indegree[method_id] == 0]
    heapq.heapify(heap)
    order: list[str] = []
    while heap:
        current = heapq.heappop(heap)
        order.append(current)
        for dependent in dependents[current]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heapq.heappush(heap, dependent)
    placed = set(order)
    order.extend(method_id for method_id in ids if method_id not in placed)
    return order


def _first_success_index(events: Sequence[dict[str, Any]]) -> dict[str, int]:
    """Per method: the observation index of its first first-attempt success."""
    seen: dict[str, int] = {}
    for index, row in enumerate(events):
        if row.get("first_attempt") is not True or row.get("success") is not True:
            continue
        method_id = str(row["method_id"])
        if method_id not in seen:
            seen[method_id] = index
    return seen


def _order_violations(
    events: Sequence[dict[str, Any]], order: Sequence[str]
) -> list[tuple[str, str, int]]:
    """Ladder pairs where the dependent succeeded first (IITA-style check).

    For every pair (m, n) with m before n in the derived order, where
    BOTH methods have first-attempt events: the pair violates when n's
    first first-attempt success was observed before m's (or m never had
    one). The log carries no timestamps, so file order is the observation
    order and one sequence yields at most one violation per pair.
    """
    attempted = {str(row["method_id"]) for row in events if row.get("first_attempt") is True}
    first_success = _first_success_index(events)
    violations: list[tuple[str, str, int]] = []
    for m_index, prereq in enumerate(order):
        for dependent in order[m_index + 1 :]:
            if prereq not in attempted or dependent not in attempted:
                continue
            dep_index = first_success.get(dependent)
            pre_index = first_success.get(prereq)
            if dep_index is not None and (pre_index is None or dep_index < pre_index):
                violations.append((prereq, dependent, 1))
    return violations


def _concept_flags(methods: Sequence[Any], rates: dict[str, tuple[int, int]]) -> list[str]:
    """Concepts whose members' first-attempt rates differ by more than 0.5."""
    from corpus_kb.analysis.method_register_progression import TEACHING_CONCEPTS

    lines: list[str] = []
    for concept in TEACHING_CONCEPTS:
        members = sorted(
            str(record.id)
            for record in methods
            if concept in (record.concepts or [])
            and rates.get(str(record.id), (0, 0))[1] > 0
        )
        if len(members) < 2:
            continue
        values = [rates[method_id][0] / rates[method_id][1] for method_id in members]
        if max(values) - min(values) > RATE_GAP_FLAG:
            detail = " ".join(
                f"{method_id}={rates[method_id][0] / rates[method_id][1]:.2f}"
                for method_id in members
            )
            lines.append(f"  {concept}: {detail}")
    return ["", "Co-struggle flags (first-attempt rate gap > 0.5, by concept):"] + sorted(lines)


def _violation_lines(violations: list[tuple[str, str, int]], cap: int) -> list[str]:
    """The order-check table: top ``cap`` pairs by (count desc, ids asc)."""
    lines = ["", f"Order check, ladder pairs where the dependent succeeded first (top {cap}):"]
    if not violations:
        lines.append("  none observed")
        return lines
    ranked = sorted(violations, key=lambda item: (-item[2], item[0], item[1]))
    for prereq, dependent, count in ranked[:cap]:
        lines.append(f"  {prereq} -> {dependent}: {count}")
    return lines


def main(argv: list[str] | None = None) -> int:
    """Print the report for the given log dir (default ``telemetry_logs``)."""
    args = sys.argv[1:] if argv is None else argv
    print(consistency_report(args[0] if args else "telemetry_logs"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
