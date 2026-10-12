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

SPLIT (adversarial review F5b): the SQL/read-model side lives in
:mod:`corpus_kb.research.release_read` and the shared config reader in
:mod:`corpus_kb.research.release.enforcement_settings` (the release service
reads the same key — one reader, two consumers). This module keeps the PURE
decision logic and re-exports the split names, so every existing import
site is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg

from corpus_kb.research.release.enforcement_settings import (  # noqa: F401  (re-export)
    ENFORCEMENT_ENFORCE,
    ENFORCEMENT_MODES,
    ENFORCEMENT_OFF,
    ENFORCEMENT_WARN,
    ReleaseGuardSettings,
    release_guard_settings,
)
from corpus_kb.research.release_read import (  # noqa: F401  (re-export)
    ACTIVE_RELEASE_SQL,
    RELEASED_STATE,
    ActiveRelease,
    ReleaseReadConn,
    active_release_for_conn,
    active_release_from_row,
    resolve_active_release,
)

UNRELEASED_DRAFT_LABEL = "unreleased_draft"
RELEASED_LABEL = "released"
SANDBOX_LABEL = "sandbox"


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
