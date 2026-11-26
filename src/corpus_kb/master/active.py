"""The one-active-project rule (W5-2): pure, stdlib only.

Spec s3: at most one project is active per tenant. Store signals (ingest,
query, analysis) outrank the folder mtime; the folder stamp counts only
when no store signal exists. The hysteresis: switching needs NEWER work
inside the switch window, while the incumbent keeps the role while its own
activity stays inside the idle window. Everything else is pool mode.

``compute_active_state`` answers slugs as None — the store resolves the
slug for the winning project (the rule works on ids alone).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

DEFAULT_SWITCH_WINDOW_S = 1800
DEFAULT_IDLE_WINDOW_S = 7200


@dataclass(frozen=True)
class ActiveSignals:
    """The freshness inputs of one project."""

    ingest_ts: datetime | None = None
    query_ts: datetime | None = None
    analysis_ts: datetime | None = None
    folder_ts: datetime | None = None


@dataclass(frozen=True)
class ActiveState:
    """The resolved answer: the winning project, its slug, and the state."""

    project_id: str | None
    slug: str | None
    state: str  # "active" or "none"


def aware_utc(ts: datetime) -> datetime:
    """View a timestamp as aware UTC (naive stamps read as UTC, never crash)."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts


def last_activity(signals: ActiveSignals) -> datetime | None:
    """The store signals' max; the folder stamp counts only with no store signal."""
    store = [
        aware_utc(ts)
        for ts in (signals.ingest_ts, signals.query_ts, signals.analysis_ts)
        if ts is not None
    ]
    if store:
        return max(store)
    if signals.folder_ts is not None:
        return aware_utc(signals.folder_ts)
    return None


def compute_active_state(
    signals: ActiveSignals,
    incumbent_id: str | None,
    incumbent_ts: datetime | None,
    challenger_id: str | None,
    challenger_signals: ActiveSignals,
    now: datetime,
    switch_window_s: int = DEFAULT_SWITCH_WINDOW_S,
    idle_window_s: int = DEFAULT_IDLE_WINDOW_S,
) -> ActiveState:
    """The active-state rule (spec s3), pure: no I/O, no clock reads.

    The challenger takes over when its last activity is at most
    ``switch_window_s`` old AND strictly newer than the incumbent's. The
    incumbent keeps the role while its own last activity is at most
    ``idle_window_s`` old. Any other answer is the pool state ("none").
    """
    now_a = aware_utc(now)
    challenger_last = last_activity(challenger_signals) if challenger_id else None
    if challenger_last is not None:
        age = (now_a - challenger_last).total_seconds()
        newer = incumbent_ts is None or challenger_last > aware_utc(incumbent_ts)
        if 0 <= age <= switch_window_s and newer:
            return ActiveState(project_id=challenger_id, slug=None, state="active")
    if incumbent_id is not None and incumbent_ts is not None:
        age = (now_a - aware_utc(incumbent_ts)).total_seconds()
        if 0 <= age <= idle_window_s:
            return ActiveState(project_id=incumbent_id, slug=None, state="active")
    return ActiveState(project_id=None, slug=None, state="none")


# The lens-world helpers (W5-2) live with the pure rule so the store file
# stays under its size ceiling: pure views over the per-project stamp map
# the store layer reads from project_activity.

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def latest_stamp(stamps: dict[str, datetime]) -> datetime | None:
    """The max of one project's kind-keyed stamps; None when it has none."""
    values = [aware_utc(ts) for ts in stamps.values() if ts is not None]
    return max(values) if values else None


def signals_from_stamps(stamps: dict[str, datetime]) -> ActiveSignals:
    """Wrap one project's kind-keyed stamps into ActiveSignals."""
    return ActiveSignals(
        ingest_ts=stamps.get("ingest"),
        query_ts=stamps.get("query"),
        analysis_ts=stamps.get("analysis"),
    )


def pick_incumbent(
    cached_id: str | None, since_ts: datetime | None, by_project: dict[str, dict[str, datetime]]
) -> tuple[str | None, datetime | None]:
    """The cached project (or the freshest one); its stamp or the cache since_ts."""
    incumbent_id = cached_id
    if incumbent_id is None and by_project:
        incumbent_id = max(by_project, key=lambda pid: latest_stamp(by_project[pid]) or _EPOCH)
    incumbent_ts = latest_stamp(by_project.get(incumbent_id, {})) if incumbent_id else None
    if incumbent_ts is None:
        incumbent_ts = since_ts
    return incumbent_id, incumbent_ts


def pick_challenger(
    by_project: dict[str, dict[str, datetime]], incumbent_id: str | None
) -> tuple[str | None, ActiveSignals]:
    """The freshest project that is not the incumbent (None when alone)."""
    others = [pid for pid in by_project if pid != incumbent_id]
    if not others:
        return None, ActiveSignals()
    challenger_id = max(others, key=lambda pid: latest_stamp(by_project[pid]) or _EPOCH)
    return challenger_id, signals_from_stamps(by_project[challenger_id])
