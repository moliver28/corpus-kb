"""The eval runner's answer section: ask, then score against references.

Records WITH a reference answer run the real ask pipeline
(``tools.ask_tools.ask`` — the ONE function every ask surface routes
through), then deepeval scores the produced answer against the
reference. Skips are honest and named: the ``--skip`` flag, a missing
``[eval]`` extra (the remediation string rides along), or no record
carrying an answer. Per-metric failures score None; nothing fabricates
a number.
"""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg

from corpus_kb.eval.golden import GoldenRecord

__all__ = ["AnswerOutcome", "run_answer_section"]

ANSWER_SKIPPED_FLAG = "answer metrics disabled by --skip-answer-metrics"
ANSWER_SKIPPED_NO_ANSWER = "no golden record carries a reference answer"

NO_DEGRADED: dict[str, object] = {"count": 0, "reasons": []}


@dataclass(frozen=True)
class AnswerOutcome:
    """The answer section's block plus its degraded-ask aggregation."""

    block: dict[str, object]
    degraded_excluded: dict[str, object]


async def run_answer_section(
    golden: list[GoldenRecord],
    cfg: dict[str, object],
    pool: asyncpg.Pool,
    skip_answer_metrics: bool,
    judge: object | None,
) -> AnswerOutcome:
    """Score produced answers against references; skip honestly otherwise."""
    if skip_answer_metrics:
        return skipped_block(ANSWER_SKIPPED_FLAG)
    scored = [record for record in golden if record.answer is not None]
    if not scored:
        return skipped_block(ANSWER_SKIPPED_NO_ANSWER)
    from corpus_kb.eval.deepeval_adapter import EvalDependencyError, build_answer_metrics
    from corpus_kb.tools.ask_tools import ask

    try:
        metrics = build_answer_metrics(cfg, judge_llm=judge)
    except EvalDependencyError as exc:
        return skipped_block(str(exc.remediation))

    per_metric: dict[str, list[float]] = {}
    excluded_count = 0
    excluded_reasons: set[str] = set()
    n_answered = 0
    for record in scored:
        envelope = await ask(record.query, config=cfg, pg_pool=pool)
        excluded_count += degraded_count(envelope)
        excluded_reasons.update(degraded_reasons(envelope))
        produced = produced_answer(envelope)
        if str(envelope.get("status")) != "success" or not produced:
            continue
        n_answered += 1
        scores = score_record(record, produced, contexts(envelope), metrics)
        for name, value in scores.items():
            if value is not None:
                per_metric.setdefault(name, []).append(value)
    return AnswerOutcome(
        answer_block(scored, n_answered, per_metric),
        {"count": excluded_count, "reasons": sorted(excluded_reasons)},
    )


def skipped_block(reason: str) -> AnswerOutcome:
    """The honest skip outcome (the remediation string rides along)."""
    return AnswerOutcome({"skipped": reason}, dict(NO_DEGRADED))


def answer_block(
    scored: list[GoldenRecord],
    n_answered: int,
    per_metric: dict[str, list[float]],
) -> dict[str, object]:
    """The answer block: means over the records that produced a score."""
    block: dict[str, object] = {"n_scored": len(scored), "n_answered": n_answered}
    for name, values in per_metric.items():
        block[name] = sum(values) / len(values)
    return block


def score_record(
    record: GoldenRecord,
    produced: str,
    contexts: list[str],
    metrics: list[object],
) -> dict[str, float | None]:
    """Score one produced answer against its golden reference."""
    from corpus_kb.eval.deepeval_adapter import score_answer

    return score_answer(record.query, produced, record.answer or "", contexts, metrics)


def produced_answer(envelope: dict[str, object]) -> str:
    result = envelope.get("result")
    if not isinstance(result, dict):
        return ""
    return str(result.get("answer", "") or "")


def contexts(envelope: dict[str, object]) -> list[str]:
    result = envelope.get("result")
    if not isinstance(result, dict):
        return []
    sources = result.get("sources")
    if not isinstance(sources, list):
        return []
    return [str(source.get("text", "")) for source in sources if isinstance(source, dict)]


def degraded_count(envelope: dict[str, object]) -> int:
    raw = envelope.get("degraded_excluded")
    if not isinstance(raw, dict):
        return 0
    return int(raw.get("count", 0) or 0)


def degraded_reasons(envelope: dict[str, object]) -> set[str]:
    raw = envelope.get("degraded_excluded")
    if not isinstance(raw, dict):
        return set()
    reasons = raw.get("reasons")
    if not isinstance(reasons, list):
        return set()
    return {str(reason) for reason in reasons}
