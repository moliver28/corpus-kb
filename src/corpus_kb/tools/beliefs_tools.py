"""MCP/HTTP/socket/CLI handlers for the belief ops (Living Corpus W4).

Four ops over the belief store — current (posterior summaries), timeline
(eras history), predict (posterior predictive), compare (side-by-side
objects for one subject). Thin envelopes; this module never writes SQL.
``beliefs_current`` rebuilds first by default so the answer reflects the
statements as of now (``rebuild: false`` reads the stored rows).
"""

from __future__ import annotations

import asyncpg

from ..errors import ValidationError
from .ingest_common import DEFAULT_TENANT_ID

__all__ = ["beliefs_compare", "beliefs_current", "beliefs_predict", "beliefs_timeline"]


def _require(value: str | None, name: str) -> str:
    if not value or not value.strip():
        raise ValidationError(
            f"{name} is required.",
            remediation=f"Pass the {name} of the belief, then retry.",
        )
    return value


async def beliefs_current(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    *,
    subject: str | None = None,
    limit: int = 50,
    rebuild: bool = True,
) -> dict[str, object]:
    """Current belief posteriors, optional subject filter."""
    from ..beliefs.store import BeliefStore

    if limit < 1:
        raise ValidationError("limit must be a positive integer.")
    store = BeliefStore(pg_pool, tenant_id)
    rebuild_note = "skipped"
    if rebuild:
        summary = await store.rebuild()
        rebuild_note = f"rebuilt {summary['beliefs']}"
    rows = await store.current(subject=subject, limit=limit)
    return {
        "status": "success",
        "beliefs": rows,
        "count": len(rows),
        "rebuild": rebuild_note,
    }


async def beliefs_timeline(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    *,
    subject: str | None = None,
    relation: str | None = None,
    object: str | None = None,
) -> dict[str, object]:
    """The era history for one belief — how the stance moved over time."""
    from ..beliefs.store import BeliefStore

    store = BeliefStore(pg_pool, tenant_id)
    view = await store.timeline(
        subject=_require(subject, "subject"),
        relation=_require(relation, "relation"),
        object_name=_require(object, "object"),
    )
    if view is None:
        raise ValidationError(
            "No such belief for this tenant.",
            remediation="Run beliefs-current to rebuild beliefs, then retry.",
        )
    return {"status": "success", **view}


async def beliefs_predict(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    *,
    subject: str | None = None,
    relation: str | None = None,
    object: str | None = None,
) -> dict[str, object]:
    """Posterior predictive: the probability the next stance is positive."""
    from ..beliefs.store import BeliefStore

    store = BeliefStore(pg_pool, tenant_id)
    view = await store.timeline(
        subject=_require(subject, "subject"),
        relation=_require(relation, "relation"),
        object_name=_require(object, "object"),
    )
    if view is None:
        raise ValidationError(
            "No such belief for this tenant.",
            remediation="Run beliefs-current to rebuild beliefs, then retry.",
        )
    post = view["posterior"]
    return {
        "status": "success",
        "subject": subject,
        "relation": relation,
        "object": object,
        "probability": post["mean"],
        "interval_low": post["interval_low"],
        "interval_high": post["interval_high"],
        "evidence_count": post["evidence_count"],
    }


async def beliefs_compare(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    *,
    subject: str | None = None,
) -> dict[str, object]:
    """Side-by-side belief posteriors for one subject across objects."""
    from ..beliefs.store import BeliefStore

    store = BeliefStore(pg_pool, tenant_id)
    rows = await store.current(subject=_require(subject, "subject"), limit=200)
    return {"status": "success", "subject": subject, "rows": rows, "count": len(rows)}
