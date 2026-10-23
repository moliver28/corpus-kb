"""Tests for deep Bayesian beliefs (Living Corpus W4): exact posterior
math, BOCPD eras, evidence building, and handler validation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from corpus_kb.beliefs import (
    build_evidence,
    eras,
    posterior,
    stance_of,
    tenant_prior,
)
from corpus_kb.beliefs.math_core import Evidence, betainc
from corpus_kb.errors import ValidationError

NOW = datetime(2026, 10, 23, tzinfo=UTC)


def _ev(stance: str, days_ago: float, weight: float = 0.9) -> Evidence:
    return Evidence(stance=stance, weight=weight, observed_at=NOW - timedelta(days=days_ago))


def test_posterior_uniform_prior_half_half() -> None:
    post = posterior([_ev("positive", 0)], now=NOW, tenant_positive_ratio=None)
    assert post.evidence_count == 1
    # tenant_prior with no ratio pools to 3.0/3.0; +0.9 positive weight.
    assert post.alpha == pytest.approx(3.9, abs=1e-4)
    assert post.beta == pytest.approx(3.0, abs=1e-4)
    assert 0.0 < post.interval_low < post.mean < post.interval_high < 1.0


def test_forgetting_lets_recent_evidence_dominate() -> None:
    old = posterior([_ev("negative", 3650, weight=1.0)], now=NOW, tenant_positive_ratio=None)
    fresh = posterior(
        [_ev("positive", 0, weight=1.0), _ev("negative", 3650, weight=1.0)],
        now=NOW,
        tenant_positive_ratio=None,
    )
    # A ten-year-old dislike barely moves the mean when a fresh like arrives.
    assert fresh.mean > 0.45
    assert old.mean < 0.5


def test_tenant_prior_pools_toward_the_tenant_ratio() -> None:
    pos_ratio = 0.9
    alpha0, beta0 = tenant_prior(pos_ratio)
    assert alpha0 > beta0
    sparse = posterior([_ev("negative", 0)], now=NOW, tenant_positive_ratio=pos_ratio)
    balanced = posterior([_ev("negative", 0)], now=NOW, tenant_positive_ratio=None)
    # A positive-leaning tenant shrinks a single negative observation up.
    assert sparse.mean > balanced.mean


def test_betainc_matches_known_values() -> None:
    assert betainc(1.0, 1.0, 0.3) == pytest.approx(0.3, abs=1e-6)
    assert betainc(2.0, 2.0, 0.5) == pytest.approx(0.5, abs=1e-6)
    assert betainc(2.0, 2.0, 1.0) == 1.0


def test_eras_split_the_ice_cream_arc() -> None:
    stream = [
        _ev("positive", 2000, weight=1.0),
        _ev("positive", 1500, weight=1.0),
        _ev("positive", 1000, weight=1.0),
        _ev("negative", 10, weight=1.0),
        _ev("negative", 0, weight=1.0),
    ]
    segments = eras(stream, shift_threshold=0.35)
    assert len(segments) == 2
    assert segments[0].mean == 1.0
    assert segments[1].mean == 0.0


def test_eras_single_consistent_stream_is_one_era() -> None:
    stream = [_ev("positive", 30 - i, weight=1.0) for i in range(5)]
    assert len(eras(stream)) == 1


def test_stance_of_and_evidence_grouping() -> None:
    assert stance_of("I really like ice cream.") == "positive"
    assert stance_of("I dislike ice cream now.") == "negative"
    assert stance_of("The weather is mild today.") is None

    rows = [
        {
            "statement_id": "s1",
            "text": "I really like ice cream.",
            "template_id": "preference",
            "confidence": 0.9,
            "entity_names": ["ice cream"],
            "exchange_id": 777,
            "superseded_by": None,
            "observed_at": NOW - timedelta(days=3650),
        },
        {
            "statement_id": "s2",
            "text": "The weather is mild.",
            "template_id": "statement",
            "confidence": 0.9,
            "entity_names": [],
            "exchange_id": None,
            "superseded_by": None,
            "observed_at": NOW,
        },
    ]
    bundles = build_evidence(rows)
    assert len(bundles) == 1
    assert bundles[0].key.subject == "speaker:777"
    assert bundles[0].key.object == "ice cream"
    assert bundles[0].items[0][1] == "s1"


def test_conflicted_evidence_is_discounted() -> None:
    rows = [
        {
            "statement_id": "s1",
            "text": "I like ice cream.",
            "template_id": "preference",
            "confidence": 0.8,
            "entity_names": ["ice cream"],
            "exchange_id": None,
            "superseded_by": None,
            "observed_at": NOW,
            "conflicted": True,
        }
    ]
    bundles = build_evidence(rows)
    ((_evidence, _sid),) = bundles[0].items
    assert _evidence.weight == pytest.approx(0.4)


@pytest.mark.asyncio
async def test_beliefs_current_rejects_bad_limit() -> None:
    from corpus_kb.tools.beliefs_tools import beliefs_current

    with pytest.raises(ValidationError, match="positive integer"):
        await beliefs_current(None, None, limit=0)


@pytest.mark.asyncio
async def test_predict_handler_requires_subject() -> None:
    from corpus_kb.tools.beliefs_tools import beliefs_predict

    with pytest.raises(ValidationError, match="subject is required"):
        await beliefs_predict(None, None)
