"""Cascade driver + Gate C judge (Living Corpus W3).

:func:`scan_statements` pairs the tenant's live statements on shared
anchors, runs the gates in cost order, and returns verdicts.
:func:`nli_judge` runs Gate C through the locked AMR NLI contract; any
degrade becomes ``uncertain`` — the flag survives, the pipeline never
fails on a judge outage.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from .cascade import CascadeVerdict, StatementRow, gate_a, gate_b

logger = logging.getLogger(__name__)

MAX_PAIRS = 400


def _rows(raw: list[dict[str, Any]]) -> list[StatementRow]:
    rows: list[StatementRow] = []
    for item in raw:
        names = item.get("entity_names") or []
        rows.append(
            StatementRow(
                statement_id=str(item["statement_id"]),
                text=str(item["text"]),
                template_id=str(item["template_id"]),
                entity_names=tuple(str(n) for n in names),
                valid_at=item.get("valid_at"),
                observed_at=item.get("observed_at"),
                superseded_by=item.get("superseded_by"),
            )
        )
    return rows


async def scan_statements(
    raw: list[dict[str, Any]],
    *,
    nli: Any | None = None,
) -> list[CascadeVerdict]:
    """Run the cascade over live statements (superseded rows excluded).

    ``nli`` is an async callable ``(a_text, b_text) -> str | None`` (Gate
    C). When absent, Gate B conflicts stand as-is and Gate C candidates
    stay ``uncertain``.
    """
    rows = [r for r in _rows(raw) if not r.superseded_by]
    by_anchor: dict[str, list[StatementRow]] = defaultdict(list)
    for row in rows:
        for name in row.entity_names:
            by_anchor[name.casefold()].append(row)

    verdicts: list[CascadeVerdict] = []
    pending_c: list[tuple[StatementRow, StatementRow, str]] = []
    for anchor, group in by_anchor.items():
        if len(group) < 2:
            continue
        # Oldest-first pairing keyed on the SAME instant Gate A compares
        # (exchange time, else ingest time): statement_a is the EARLIER
        # claim, so a revision resolve supersedes the outdated statement.
        # statement_id breaks timestamp ties deterministically (rule 9).
        group.sort(
            key=lambda r: (
                r.valid_at or r.observed_at or datetime.max.replace(tzinfo=UTC),
                r.statement_id,
            )
        )
        for i, a in enumerate(group):
            for b in group[i + 1 :]:
                if len(verdicts) + len(pending_c) >= MAX_PAIRS:
                    # Budget spent: stop pairing. Gate C candidates still
                    # flush below (within the same budget) — never dropped.
                    break
                verdict = gate_a(a, b, anchor)
                if verdict is None:
                    verdict = gate_b(a, b, anchor)
                    if verdict is None and nli is not None:
                        pending_c.append((a, b, anchor))
                    elif verdict is not None:
                        verdicts.append(verdict)
                else:
                    verdicts.append(verdict)
            if len(verdicts) + len(pending_c) >= MAX_PAIRS:
                break

    budget = max(MAX_PAIRS - len(verdicts), 0)
    verdicts.extend(await _gate_c(pending_c[:budget], nli))
    return verdicts


async def _gate_c(
    pending: list[tuple[StatementRow, StatementRow, str]],
    nli: Any,
) -> list[CascadeVerdict]:
    out: list[CascadeVerdict] = []
    for a, b, anchor in pending:
        verdict = await nli(a.text, b.text)
        out.append(
            CascadeVerdict(
                statement_a_id=a.statement_id,
                statement_b_id=b.statement_id,
                anchor_name=anchor,
                kind="temporal",
                gate_reached="C",
                verdict="conflict" if verdict == "CONTRADICT" else "uncertain",
                nli_verdict=verdict,
            )
        )
    return out


async def nli_judge(a_text: str, b_text: str, model: str, base_url: str) -> str | None:
    """One locked NLI judgment; None on any degrade (honest uncertain)."""
    from ..analysis.nli_contract import NliError, build_contradiction_prompt, judge_verdict

    try:
        return await judge_verdict(build_contradiction_prompt(a_text, b_text), model, base_url)
    except NliError as exc:
        logger.warning("NLI judge degraded: %s", exc)
        return None
