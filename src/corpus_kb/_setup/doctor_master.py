"""The doctor's master-topology section (W5-6, spec s16 row W5)."""

from __future__ import annotations

import asyncio
from typing import Any

from corpus_kb._setup.doctor_engine import resolved_backend
from corpus_kb._setup.doctor_research import (
    STATUS_INFO,
    STATUS_OK,
    STATUS_SKIPPED,
    ResearchCheck,
)

ACTIVE_NONE_DETAIL = "No active project; queries cover the whole pool."
DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


async def check_master(gateway_or_none: Any, cfg: dict[str, Any]) -> list[ResearchCheck]:
    """The master-state rows: workspace line, active project line, count.

    Reads the cached ``master_state`` row and the derived
    ``master_project_index`` view through the gateway (both engines).
    ``gateway_or_none`` None answers one skipped row: the store is
    unreachable, so the doctor says so instead of guessing.
    ``master.adopt_on_start``/``master.adopt_skip`` (W8) and the
    backup/scan keys (W7) get their readers in later waves; this
    section reports state only.
    """
    workspace_root = str((cfg.get("master") or {}).get("workspace_root", ""))
    checks: list[ResearchCheck] = [workspace_check(workspace_root)]
    if gateway_or_none is None:
        checks.append(
            ResearchCheck(
                name="Master state",
                status=STATUS_SKIPPED,
                detail="Store unreachable; the active project needs the store.",
            )
        )
        return checks
    async with gateway_or_none.transaction(DEFAULT_TENANT) as tx:
        row = await tx.fetchrow("SELECT active_project_id FROM master_state LIMIT 1")
        index_row = await tx.fetchrow("SELECT count(*) AS project_count FROM master_project_index")
    if row is None or row["active_project_id"] is None:
        checks.append(
            ResearchCheck(name="Master state", status=STATUS_INFO, detail=ACTIVE_NONE_DETAIL)
        )
        return checks
    slug = await _slug_for(gateway_or_none, row["active_project_id"])
    detail = f"Active project: {slug}." if slug else ACTIVE_NONE_DETAIL
    checks.append(ResearchCheck(name="Master state", status=STATUS_OK, detail=detail))
    if index_row is not None:
        checks.append(
            ResearchCheck(
                name="Master projects",
                status=STATUS_INFO,
                detail=f"{index_row['project_count']} project(s) registered.",
            )
        )
    return checks


def workspace_check(workspace_root: str) -> ResearchCheck:
    """Attribution on -> ok with the root; off -> plain info, never a warn."""
    if workspace_root:
        return ResearchCheck(
            name="Master workspace",
            status=STATUS_OK,
            detail=f"Attribution on: {workspace_root}",
        )
    return ResearchCheck(
        name="Master workspace",
        status=STATUS_INFO,
        detail="Attribution off (master.workspace_root empty).",
    )


async def _slug_for(gateway: Any, project_id: object) -> str | None:
    async with gateway.transaction(DEFAULT_TENANT) as tx:
        row = await tx.fetchrow(
            "SELECT slug FROM research_projects WHERE project_id = $1",
            str(project_id),
        )
    return str(row["slug"]) if row is not None and row["slug"] is not None else None


async def master_section(config: dict[str, Any], conn_str: str) -> list[ResearchCheck]:
    """Open-or-skip: the doctor section without leaking a gateway.

    DuckDB connects to the analytics file; Postgres opens a tiny pool.
    Any failure answers the skipped rows instead of failing the doctor.
    """
    gateway = None
    try:
        if resolved_backend(config) == "duckdb":
            from corpus_kb.storage.gateway import DuckDbGateway

            gateway = await DuckDbGateway.connect(conn_str)
        elif conn_str:
            import asyncpg

            from corpus_kb.storage.gateway import PostgresGateway

            pool = await asyncpg.create_pool(dsn=conn_str, min_size=0, max_size=2, timeout=3)
            # A min_size=0 pool connects lazily: probe now, inside the guard,
            # or the first transaction would raise past the doctor flow.
            async with pool.acquire() as conn:
                await conn.execute("SELECT 1")
            gateway = PostgresGateway(pool)
    except Exception:
        gateway = None
    if gateway is None:
        return await check_master(None, config)
    try:
        return await check_master(gateway, config)
    finally:
        closer = getattr(gateway, "close", None)
        if closer is not None:
            outcome = closer()
            if asyncio.iscoroutine(outcome):
                await outcome
