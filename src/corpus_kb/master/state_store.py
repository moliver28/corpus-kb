"""Cached active-project state (W5-2): gateway reads/writes and the sidecar.

All SQL runs through the ``SqlGateway`` seam inside the tenant
transaction, so both engines answer identically (Postgres adds the RLS
guard). ``resolve_lens`` reads ``master_state`` + ``project_activity``,
runs the pure rule, and rewrites the cache row and the sidecar only on a
state change; W6's hooks read the sidecar file only, never the database.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from corpus_kb.master.active import (
    DEFAULT_IDLE_WINDOW_S,
    DEFAULT_SWITCH_WINDOW_S,
    aware_utc,
    compute_active_state,
    pick_challenger,
    pick_incumbent,
)
from corpus_kb.master.attribution import master_cfg
from corpus_kb.storage.gateway import SqlGateway, as_gateway

SIDECAR_FILENAME = "master_state.json"
_ACTIVITY_KINDS = frozenset({"ingest", "query", "analysis"})


def sidecar_path(cfg: dict[str, Any] | None) -> Path:
    """The sidecar file under the installer data dir (env var wins)."""
    data_dir = os.environ.get("CORPUS_KB_INSTALL_DATA_DIR")
    if not data_dir:
        installer = (cfg or {}).get("installer")
        root = installer.get("data_dir") if isinstance(installer, dict) else None
        data_dir = str(root or "~/.corpus-kb")
    return Path(data_dir).expanduser() / SIDECAR_FILENAME


def write_sidecar(cfg: dict[str, Any] | None, active_slug: str | None, now: datetime) -> None:
    """Atomically write the sidecar (temp file + replace)."""
    path = sidecar_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"active_project": active_slug, "updated_ts": aware_utc(now).isoformat()}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)


async def project_id_for_slug(gateway: SqlGateway | Any, tenant_id: str, slug: str) -> str:
    """The project id for a workspace slug, registering the row when unknown."""
    gw = as_gateway(gateway)
    async with gw.transaction(tenant_id) as tx:
        row = await tx.fetchrow(
            "SELECT project_id FROM research_projects WHERE tenant_id = $1 AND slug = $2",
            tenant_id,
            slug,
        )
        if row is not None:
            return str(row["project_id"])
        # ON CONFLICT + re-select: ingest fans files out concurrently, so
        # two workers can race the same fresh slug; the loser re-selects
        # the winner's row instead of dying on the unique index.
        fresh = str(uuid.uuid4())
        await tx.execute(
            "INSERT INTO research_projects (project_id, tenant_id, name, slug)"
            " VALUES ($1, $2, $3, $4) ON CONFLICT (tenant_id, slug) DO NOTHING",
            fresh,
            tenant_id,
            slug,
            slug,
        )
        row = await tx.fetchval(
            "SELECT project_id FROM research_projects WHERE tenant_id = $1 AND slug = $2",
            tenant_id,
            slug,
        )
        if row is None:
            raise LookupError(f"project row missing after registration: {slug}")
        return str(row)


async def project_id_only(gateway: SqlGateway | Any, tenant_id: str, slug: str) -> str | None:
    """The existing project id for a slug, or None. Never registers a row.

    The query-side twin of ``project_id_for_slug``: an explicit search
    scope names a project that must already exist (ingest owns
    registration), so a lookup here stays read-only.
    """
    gw = as_gateway(gateway)
    async with gw.transaction(tenant_id) as tx:
        row = await tx.fetchrow(
            "SELECT project_id FROM research_projects WHERE tenant_id = $1 AND slug = $2",
            tenant_id,
            slug,
        )
    return str(row["project_id"]) if row is not None else None


async def note_activity(
    gateway: SqlGateway | Any, tenant_id: str, project_id: str, kind: str, now: datetime
) -> None:
    """Upsert one activity stamp (kinds: ingest, query, analysis)."""
    if kind not in _ACTIVITY_KINDS:
        raise ValueError(f"unknown activity kind: {kind}")
    gw = as_gateway(gateway)
    async with gw.transaction(tenant_id) as tx:
        await tx.execute(
            "INSERT INTO project_activity (tenant_id, project_id, kind, last_ts)"
            " VALUES ($1, $2, $3, $4)"
            " ON CONFLICT (tenant_id, project_id, kind) DO UPDATE SET last_ts = EXCLUDED.last_ts",
            tenant_id,
            project_id,
            kind,
            aware_utc(now),
        )


async def _read_lens_world(
    tx: SqlGateway, tenant_id: str
) -> tuple[str | None, datetime | None, dict[str, dict[str, datetime]]]:
    """Read the cache row and the per-project activity stamps (tenant-scoped)."""
    cache = await tx.fetchrow(
        "SELECT active_project_id, since_ts FROM master_state WHERE tenant_id = $1", tenant_id
    )
    rows = await tx.fetch(
        "SELECT project_id, kind, last_ts FROM project_activity WHERE tenant_id = $1", tenant_id
    )
    cached_id: str | None = None
    since_ts: datetime | None = None
    if cache is not None:
        if cache["active_project_id"] is not None:
            cached_id = str(cache["active_project_id"])
        if cache["since_ts"] is not None:
            since_ts = cache["since_ts"]
    by_project: dict[str, dict[str, datetime]] = {}
    for row in rows:
        project = str(row["project_id"])
        by_project.setdefault(project, {})[str(row["kind"])] = row["last_ts"]
    return cached_id, since_ts, by_project


async def resolve_lens(
    gateway: SqlGateway | Any, cfg: dict[str, Any] | None, tenant_id: str, now: datetime
) -> tuple[str | None, str | None]:
    """Resolve the one active project for the tenant; cache + mirror it.

    Answers ``(active_project_id, active_slug)``. The cache row and the
    sidecar change only when the winning project changes.
    Attribution off (``master.workspace_root`` empty) answers
    ``(None, None)`` without touching the gateway. The windows come
    from ``master.idle_window_s`` and ``master.switch_window_s``.
    """
    if not master_cfg(cfg).get("workspace_root"):
        return None, None
    gw = as_gateway(gateway)
    master = master_cfg(cfg)
    switch = int(master.get("switch_window_s", DEFAULT_SWITCH_WINDOW_S))
    idle = int(master.get("idle_window_s", DEFAULT_IDLE_WINDOW_S))
    async with gw.transaction(tenant_id) as tx:
        cached_id, since_ts, by_project = await _read_lens_world(tx, tenant_id)
        incumbent_id, incumbent_ts = pick_incumbent(cached_id, since_ts, by_project)
        challenger_id, challenger_signals = pick_challenger(by_project, incumbent_id)
        state = compute_active_state(
            incumbent_id,
            incumbent_ts,
            challenger_id,
            challenger_signals,
            now,
            switch_window_s=switch,
            idle_window_s=idle,
        )
        active_id = state.project_id
        slug = await _slug_for(tx, tenant_id, active_id)
        changed = active_id != cached_id
        if changed:
            await _write_cache_row(tx, tenant_id, active_id, now)
    if changed:
        write_sidecar(cfg, slug, now)
    return (active_id, slug)


async def _slug_for(tx: SqlGateway, tenant_id: str, project_id: str | None) -> str | None:
    """The project's slug, or None when the row is missing or unslugged."""
    if project_id is None:
        return None
    row = await tx.fetchrow(
        "SELECT slug FROM research_projects WHERE project_id = $1 AND tenant_id = $2",
        project_id,
        tenant_id,
    )
    if row is not None and row["slug"] is not None:
        return str(row["slug"])
    return None


async def _write_cache_row(
    tx: SqlGateway, tenant_id: str, active_id: str | None, now: datetime
) -> None:
    """Insert or rewrite the tenant's cached active project."""
    await tx.execute(
        "INSERT INTO master_state (tenant_id, active_project_id, since_ts, updated_ts)"
        " VALUES ($1, $2, $3, $4)"
        " ON CONFLICT (tenant_id) DO UPDATE SET active_project_id = EXCLUDED.active_project_id,"
        " since_ts = EXCLUDED.since_ts, updated_ts = EXCLUDED.updated_ts",
        tenant_id,
        active_id,
        aware_utc(now) if active_id else None,
        aware_utc(now),
    )


async def stamp_project_touch(
    gateway: SqlGateway | Any, cfg: dict[str, Any] | None, tenant_id: str, kind: str, now: datetime
) -> None:
    """Stamp the CACHED active project only — a cheap read, no recompute."""
    if not master_cfg(cfg).get("workspace_root"):
        return
    if kind not in _ACTIVITY_KINDS:
        raise ValueError(f"unknown activity kind: {kind}")
    gw = as_gateway(gateway)
    async with gw.transaction(tenant_id) as tx:
        row = await tx.fetchrow(
            "SELECT active_project_id FROM master_state WHERE tenant_id = $1", tenant_id
        )
        if row is None or row["active_project_id"] is None:
            return
        project_id = str(row["active_project_id"])
        await tx.execute(
            "INSERT INTO project_activity (tenant_id, project_id, kind, last_ts)"
            " VALUES ($1, $2, $3, $4)"
            " ON CONFLICT (tenant_id, project_id, kind) DO UPDATE SET last_ts = EXCLUDED.last_ts",
            tenant_id,
            project_id,
            kind,
            aware_utc(now),
        )
