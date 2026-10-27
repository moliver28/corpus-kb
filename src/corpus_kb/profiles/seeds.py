"""Seeds-file reads and writes, plus the profile-file point-in.

The exemplar arm of ``extraction_tune`` mines confirmed corpus
decisions and lands them as LangExtract seed exemplars next to the
profile, then points the profile at the file (creating a minimal
profile when none exists yet — the paved path: one op call, zero
hand-edited YAML). Writes are deterministic: sorted keys, stable
order, trailing newline. Every path is containment-checked against
the profiles directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..errors import ValidationError
from .loader import IngestionProfile, profiles_dir, safe_profile_path

MINIMAL_PROFILE: dict[str, Any] = {
    "name": "",
    "description": "Authored by extraction-tune; edit freely.",
    "data_type": "mixed",
}


def write_seeds(profiles_dir_path: str, seeds_name: str, exemplars: list[dict[str, Any]]) -> str:
    """Write exemplars as a seeds YAML file; returns the written path."""
    path = Path(safe_profile_path(profiles_dir_path, seeds_name))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({"exemplars": exemplars}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return str(path)


def point_profile_at_seeds(
    profiles_dir_path: str, profile_name: str, seeds_name: str
) -> tuple[str, bool]:
    """Set ``seeds_file`` on the profile YAML, creating it when absent.

    Returns (profile_path, created). Existing keys are preserved; the
    file is rewritten (YAML round-trips lose comments — profiles are
    machine-authored artifacts, so that is accepted).
    """
    path = Path(safe_profile_path(profiles_dir_path, f"{profile_name}.yaml"))
    created = not path.exists()
    if created:
        raw = {**MINIMAL_PROFILE, "name": profile_name}
    else:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValidationError(
                f"Profile file {path} must be a YAML mapping.",
                remediation="Fix or remove the file, then re-run extraction-tune.",
            )
    raw["seeds_file"] = seeds_name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return str(path), created


def profile_seeds_path(config: dict[str, Any], profile: IngestionProfile) -> str:
    """Absolute path of the profile's seeds file, if it declares one."""
    if profile.seeds_file is None:
        return ""
    return safe_profile_path(profiles_dir(config), profile.seeds_file)


def load_seeds(config: dict[str, Any], profile: IngestionProfile) -> list[dict[str, Any]]:
    """Load the profile's LangExtract seed exemplars.

    A declared-but-missing seeds file is a ValidationError (a profile is
    a contract, not a preference — validate_profiles reports the same
    condition); a profile WITHOUT a seeds_file simply has no seeds.
    """
    path = profile_seeds_path(config, profile)
    if not path:
        return []
    seeds_file = Path(path)
    if not seeds_file.exists():
        raise ValidationError(
            f"Profile {profile.name!r} declares seeds_file"
            f" {profile.seeds_file!r} but it is missing.",
            remediation="Run extraction-tune to write the seeds, or remove the seeds_file key.",
        )
    raw = yaml.safe_load(seeds_file.read_text(encoding="utf-8"))
    exemplars = raw.get("exemplars") if isinstance(raw, dict) else None
    if not isinstance(exemplars, list):
        raise ValidationError(
            f"Seeds file {path} must hold an 'exemplars' list.",
            remediation="Write extraction_tune output, or remove the seeds_file key.",
        )
    return [e for e in exemplars if isinstance(e, dict)]
