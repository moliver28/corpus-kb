"""MCP/HTTP/socket/CLI handlers for the conflict ops (Living Corpus W3).

``conflicts_scan`` runs the cascade over the tenant's live statements
and records the flags. ``conflicts_list`` reads them. A shared handler
per op, thin envelopes — this module never writes SQL.
"""

from __future__ import annotations

import asyncpg

from ..errors import ValidationError
from ..utils.config_sections import mapping_section
from .ingest_common import DEFAULT_TENANT_ID

__all__ = ["conflicts_list", "conflicts_resolve", "conflicts_scan"]

_BAD_VERDICT_REASON = "resolved_verdict must be conflict, revision, or dismissed."
_BAD_VERDICT_REMEDY = "Pass one of: conflict, revision, dismissed."


def _check_verdict(resolved_verdict: str) -> None:
    if resolved_verdict not in ("conflict", "revision", "dismissed"):
        raise ValidationError(_BAD_VERDICT_REASON, remediation=_BAD_VERDICT_REMEDY)


async def conflicts_scan(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, object]:
    """Run the contradiction cascade; record flags; return the summary.

    Returns ``{status, scanned, flagged, verdicts}`` where ``verdicts``
    counts by verdict word. Gate C uses the locked NLI judge when the
    Ollama endpoint answers; a judge outage degrades to ``uncertain``.
    """
    from ..conflicts import ConflictStore, nli_judge, scan_statements

    store = ConflictStore(pg_pool, tenant_id)
    raw = await store.fetch_scan_rows()
    uncertainty = mapping_section(mapping_section(config, "coding"), "uncertainty")
    model = str(uncertainty.get("model") or "qwen3:4b")
    base_url = str(uncertainty.get("base_url") or "http://localhost:11434")

    async def nli(a_text: str, b_text: str) -> str | None:
        return await nli_judge(a_text, b_text, model, base_url)

    verdicts = await scan_statements(raw, nli=nli)
    written = await store.record(verdicts)
    counts: dict[str, int] = {}
    for verdict in verdicts:
        counts[verdict.verdict] = counts.get(verdict.verdict, 0) + 1
    return {
        "status": "success",
        "scanned": len(raw),
        "flagged": written,
        "verdicts": dict(sorted(counts.items())),
    }


async def conflicts_list(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    *,
    verdict: str | None = None,
    limit: int = 50,
) -> dict[str, object]:
    """Deterministic newest-first page of recorded conflict flags."""
    from ..conflicts import ConflictStore

    if limit < 1:
        raise ValidationError("limit must be a positive integer.")
    store = ConflictStore(pg_pool, tenant_id)
    rows = await store.list_conflicts(verdict=verdict, limit=limit)
    return {"status": "success", "conflicts": rows, "count": len(rows)}


async def conflicts_resolve(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    *,
    conflict_id: str | None = None,
    resolved_verdict: str | None = None,
    resolved_by: str | None = None,
) -> dict[str, object]:
    """Finalize one flag; a confirmed revision supersedes the older claim."""
    from ..conflicts import ConflictStore

    _check_verdict(resolved_verdict or "")
    flag_id = (conflict_id or "").strip()
    reviewer = (resolved_by or "").strip()
    if not flag_id or not reviewer:
        raise ValidationError(
            "conflict_id and resolved_by are both required.",
            remediation="Pass the conflict flag id and the reviewer name, then retry.",
        )
    store = ConflictStore(pg_pool, tenant_id)
    row = await store.resolve(flag_id, resolved_verdict or "", reviewer)
    if row is None:
        raise ValidationError(
            f"Conflict {flag_id} not found for this tenant.",
            remediation="Run conflicts-scan first, then resolve a listed conflict id.",
        )
    return {"status": "success", "conflict_id": flag_id, "resolved_verdict": resolved_verdict}
