"""Shared ``research.release.{enforcement, default_profile}`` config reader.

NEUTRAL ground for both consumers of the enforcement mode (adversarial
review F3/F5b): the U24 deduction guard blocks deductive runs with it and
the release service gates ``release`` with it, so the reader lives here —
outside the guard — and both import it. Config is the single source of
truth; the per-profile ``enforcement`` field stays a documented DEFAULT
(warn), never the applied mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from corpus_kb.research.release.profiles import (
    DEFAULT_PROFILE,
    ENFORCEMENT_ENFORCE,
    ENFORCEMENT_MODES,
    ENFORCEMENT_OFF,
    ENFORCEMENT_WARN,
)

__all__ = [
    "ENFORCEMENT_ENFORCE",
    "ENFORCEMENT_MODES",
    "ENFORCEMENT_OFF",
    "ENFORCEMENT_WARN",
    "ReleaseGuardSettings",
    "release_guard_settings",
]


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
