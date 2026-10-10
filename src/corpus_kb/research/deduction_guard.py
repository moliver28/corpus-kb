"""U24 deduction guard (v6 §9) — release enforcement for deductive runs.

Before a deductive run scores units, the guard resolves the ACTIVE release
for the target codebook version through the release layer's read path (the
``codebook_releases`` read table, migration 019, projected by
``ReleaseProjector``) and applies ``research.release.enforcement`` — read
from CONFIG via :func:`release_guard_settings`, which is what makes the
pre-staged key live:

  * off      — behavior unchanged; nothing recorded, nothing blocks;
  * warn     — the run CONTINUES; the active release hash is recorded in the
               run output, or an ``unreleased_draft`` warning lands there;
  * enforce  — an unreleased codebook version BLOCKS the run (honest
               ``blocked_unreleased_draft`` status, no run aggregate is
               started) unless the caller passes ``allow_draft``; a bypass
               run is labeled ``sandbox`` in its run metadata.

Enforcement never invents evidence: a missing release row is reported as
such, never reinterpreted as a released one, and ``warn`` never blocks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

import asyncpg

from corpus_kb.research.release.profiles import (
    DEFAULT_PROFILE,
    ENFORCEMENT_ENFORCE,
    ENFORCEMENT_MODES,
    ENFORCEMENT_OFF,
    ENFORCEMENT_WARN,
)

RELEASED_STATE = "released"

UNRELEASED_DRAFT_LABEL = "unreleased_draft"
RELEASED_LABEL = "released"
SANDBOX_LABEL = "sandbox"

ACTIVE_RELEASE_SQL = """
SELECT release_id, state, profile, manifest_sha256, codebook_version_sha256,
       released_at
FROM codebook_releases
WHERE tenant_id = $1 AND codebook_version = $2
ORDER BY (state = 'released') DESC, released_at DESC NULLS LAST, release_id DESC
LIMIT 1
"""


@dataclass(frozen=True)
class ReleaseGuardSettings:
    """Typed ``research.release`` reads the guard applies (config is source)."""

    enforcement: str = ENFORCEMENT_WARN
    default_profile: str = DEFAULT_PROFILE


def release_guard_settings(config: dict[str, Any]) -> ReleaseGuardSettings:
    """Read ``research.release.{enforcement, default_profile}`` fail-loud.

    Unknown enforcement values and non-mapping blocks raise ValueError so a
    misconfigured YAML cannot silently downgrade the guard to ``warn``.
    """
    research = config.get("research")
    if research is not None and not isinstance(research, dict):
        raise ValueError("config.research must be a mapping")
    release = (research or {}).get("release") or {}
    if not isinstance(release, dict):
        raise ValueError("config.research.release must be a mapping")
    enforcement = str(release.get("enforcement") or ENFORCEMENT_WARN)
    if enforcement not in ENFORCEMENT_MODES:
        raise ValueError(
            f"research.release.enforcement must be one of {list(ENFORCEMENT_MODES)}, "
            f"got {enforcement!r}"
        )
    profile = str(release.get("default_profile") or DEFAULT_PROFILE)
    return ReleaseGuardSettings(enforcement=enforcement, default_profile=profile)


@dataclass(frozen=True)
class ActiveRelease:
    """The release row resolved for one codebook version (may be a draft)."""

    release_id: str
    state: str
    manifest_sha256: str
    codebook_version_sha256: str
    released_at: str | None
    profile: str

    @property
    def is_released(self) -> bool:
        return self.state == RELEASED_STATE


class ReleaseReadConn(Protocol):
    """Minimal asyncpg.Connection surface the release read needs (test-fake
    friendly, mirroring exact_cache.CacheConn)."""

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        """Run one statement, return the first row or None."""
        ...


def active_release_from_row(row: Any) -> ActiveRelease | None:
    """Map one ``codebook_releases`` row (or None) to an ActiveRelease."""
    if row is None:
        return None
    released_at = row["released_at"]
    return ActiveRelease(
        release_id=str(row["release_id"]),
        state=str(row["state"]),
        manifest_sha256=str(row["manifest_sha256"] or ""),
        codebook_version_sha256=str(row["codebook_version_sha256"] or ""),
        released_at=str(released_at) if released_at is not None else None,
        profile=str(row["profile"] or ""),
    )


async def active_release_for_conn(
    conn: ReleaseReadConn, tenant_id: UUID, codebook_version_id: UUID
) -> ActiveRelease | None:
    """The release row resolved for one version under an open tenant scope."""
    row = await conn.fetchrow(
        ACTIVE_RELEASE_SQL, UUID(str(tenant_id)), UUID(str(codebook_version_id))
    )
    return active_release_from_row(row)


async def resolve_active_release(
    pool: asyncpg.Pool, tenant_id: UUID, codebook_version_id: UUID
) -> ActiveRelease | None:
    """Resolve the release row for one version via the release read tables."""
    from typing import cast

    from corpus_kb.storage.tenant_conn import tenant_connection

    async with tenant_connection(pool, tenant_id) as conn:
        # asyncpg.Connection satisfies the read protocol structurally (its
        # stub is untyped, so the cast documents the member, not a lie).
        reader = cast("ReleaseReadConn", conn)
        return await active_release_for_conn(reader, tenant_id, codebook_version_id)


@dataclass(frozen=True)
class GuardDecision:
    """The guard's verdict for one deductive run (U24)."""

    action: str  # "run" | "block"
    enforcement: str
    label: str  # "" (off) | released | unreleased_draft | sandbox
    reason: str
    release: ActiveRelease | None = None
    warnings: tuple[str, ...] = ()

    def summary_block(self) -> dict[str, object]:
        """The run-output block (label + hash or the honest warning set)."""
        block: dict[str, object] = {
            "enforcement": self.enforcement,
            "label": self.label,
            "warnings": list(self.warnings),
        }
        if self.release is not None and self.release.is_released:
            block["release_id"] = self.release.release_id
            block["manifest_sha256"] = self.release.manifest_sha256
        return block


def _unreleased_warning(version_id: UUID, release: ActiveRelease | None) -> str:
    if release is None:
        return (
            f"{UNRELEASED_DRAFT_LABEL}: codebook version {version_id} has no release "
            "row at all (nothing was ever requested) - results are NOT backed by "
            "a released codebook"
        )
    return (
        f"{UNRELEASED_DRAFT_LABEL}: the latest release for codebook version "
        f"{version_id} is {release.release_id} in state {release.state!r}, not "
        "released - results are NOT backed by a released codebook"
    )


def decide_guard(
    active: ActiveRelease | None,
    settings: ReleaseGuardSettings,
    version_id: UUID,
    *,
    allow_draft: bool = False,
) -> GuardDecision:
    """Pure U24 semantics: off unchanged, warn warns, enforce blocks.

    ``allow_draft`` only matters under ``enforce``: it bypasses the block and
    labels the run ``sandbox``. Under ``off``/``warn`` it is ignored (warn
    already runs, and off records nothing).
    """
    if settings.enforcement == ENFORCEMENT_OFF:
        return GuardDecision(
            action="run", enforcement=ENFORCEMENT_OFF, label="", reason="enforcement off"
        )
    if active is not None and active.is_released:
        return GuardDecision(
            action="run",
            enforcement=settings.enforcement,
            label=RELEASED_LABEL,
            reason=f"active release {active.release_id} is released",
            release=active,
        )
    warning = _unreleased_warning(version_id, active)
    if settings.enforcement == ENFORCEMENT_WARN:
        return GuardDecision(
            action="run",
            enforcement=ENFORCEMENT_WARN,
            label=UNRELEASED_DRAFT_LABEL,
            reason=warning,
            release=active,
            warnings=(warning,),
        )
    if allow_draft:
        bypass = (
            f"sandbox: running against unreleased codebook version {version_id} "
            "(--allow-draft bypass of enforcement=enforce); treat results as sandbox"
        )
        return GuardDecision(
            action="run",
            enforcement=ENFORCEMENT_ENFORCE,
            label=SANDBOX_LABEL,
            reason=bypass,
            release=active,
            warnings=(bypass,),
        )
    detail = (
        "no release exists for this codebook version"
        if active is None
        else f"latest release {active.release_id} is in state {active.state!r}"
    )
    return GuardDecision(
        action="block",
        enforcement=ENFORCEMENT_ENFORCE,
        label=UNRELEASED_DRAFT_LABEL,
        reason=(
            f"enforcement=enforce blocks deductive runs on unreleased codebook versions: {detail}"
        ),
        release=active,
    )


async def guard_deductive_run(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    codebook_version_id: UUID,
    config: dict[str, Any],
    *,
    allow_draft: bool = False,
) -> GuardDecision:
    """Compose the guard: settings from config + active release + decision."""
    settings = release_guard_settings(config)
    active = await resolve_active_release(pool, tenant_id, codebook_version_id)
    return decide_guard(active, settings, codebook_version_id, allow_draft=allow_draft)
