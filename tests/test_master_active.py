"""The one-active-project rule (W5-2): the pure compute_active_state contract.

Store signals outrank the folder stamp; the challenger needs strictly
newer work inside the switch window; the incumbent keeps the role while
its own activity stays inside the idle window.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from corpus_kb.master.active import ActiveSignals, compute_active_state, last_activity

NOW = datetime(2026, 11, 25, 12, 0, 0, tzinfo=UTC)
INC = "11111111-1111-1111-1111-111111111111"
CHL = "22222222-2222-2222-2222-222222222222"


def _ago(seconds: float) -> datetime:
    return NOW - timedelta(seconds=seconds)


def test_incumbent_stays_within_idle_window() -> None:
    state = compute_active_state(
        INC,
        _ago(3600),
        None,
        ActiveSignals(),
        NOW,
    )
    assert state.project_id == INC
    assert state.state == "active"


def test_challenger_takes_over_when_strictly_newer() -> None:
    state = compute_active_state(
        INC,
        _ago(3600),
        CHL,
        ActiveSignals(query_ts=_ago(100)),
        NOW,
    )
    assert state.project_id == CHL
    assert state.state == "active"


def test_equal_timestamps_keep_incumbent() -> None:
    """Review focus 3: same age, same stamp — the challenger is not strictly newer."""
    ts = _ago(100)
    state = compute_active_state(
        INC,
        ts,
        CHL,
        ActiveSignals(query_ts=ts),
        NOW,
    )
    assert state.project_id == INC


def test_folder_signal_never_outranks_store() -> None:
    signals = ActiveSignals(ingest_ts=_ago(5000), folder_ts=_ago(10))
    assert last_activity(signals) == _ago(5000)
    # With no store signal at all, the folder stamp is the answer.
    assert last_activity(ActiveSignals(folder_ts=_ago(10))) == _ago(10)


def test_no_fresh_signals_answers_none() -> None:
    """Incumbent past the idle window and the challenger past the switch window."""
    state = compute_active_state(
        INC,
        _ago(9000),
        CHL,
        ActiveSignals(query_ts=_ago(2000)),
        NOW,
    )
    assert state.project_id is None
    assert state.state == "none"
