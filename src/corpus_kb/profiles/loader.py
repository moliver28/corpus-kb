"""Profile loading and the one merge point into the pipeline config.

A profile file (``profiles/<name>.yaml``) is strict: unknown keys,
unknown enums, and a mismatched ``name`` all fail loudly (the house
pattern — never a silent fallback). Relative seed paths resolve against
the profiles directory, so a profile is portable with its corpus.

Data types are corpus labels, not pipelines. ``product``, ``engineering``,
and ``design`` (Wave 6) label the shipped starter profiles for those
audiences. There is no ``research`` type on purpose: research transcripts
stay on ``interview``/``focus_group``, which the research ingest path
already serves turn by turn.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..errors import ValidationError
from .blocks import parse_extraction, parse_ingestion
from .calibrate_spec import CalibrateSpec, parse_calibrate

DATA_TYPES: frozenset[str] = frozenset(
    {
        "interview",
        "focus_group",
        "survey",
        "news",
        "mixed",
        # Audience types (unified-ingestion Wave 6): labels for the shipped
        # starter profiles. Research transcripts keep `interview` and
        # `focus_group` — the research path already ingests them turn by
        # turn, so no separate research data type exists.
        "product",
        "engineering",
        "design",
    }
)
# Mirrors tools.pipeline.validate.MODES (kept literal so profiles never
# imports the tools layer; test_profiles pins the two in lockstep).
PROFILE_VALIDATE_MODES: frozenset[str] = frozenset(
    {"off", "advisory", "enforce", "calibrate", "transcript_meta"}
)
KNOWN_TOP_KEYS: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "data_type",
        "ingestion",
        "extraction",
        "templates",
        "seeds_file",
        "validate",
        "calibrate",
    }
)

DEFAULT_PROFILES_DIR = "profiles"

# Profile and seed names are plain file names: letters/digits first, then
# letters, digits, dots, dashes, underscores. Every path part must match,
# and the joined path is containment-checked, so nothing escapes the
# profiles directory. Multi-part leaves (a seeds/ subfolder) are fine.
_SAFE_LEAF_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def safe_profile_path(base_dir: str, leaf: str) -> str:
    """Join ``leaf`` under ``base_dir``, refusing any escape from it.

    Profile names and seed file names arrive from configs, ops, and
    profile YAML; directory parts are refused outright and the joined
    path is containment-checked, so nothing outside the profiles
    directory is ever read or written.
    """
    parts = leaf.replace("\\", "/").split("/")
    if not parts or not all(_SAFE_LEAF_PART.match(p) for p in parts):
        raise ValidationError(
            f"Name {leaf!r} is not a safe profile file name.",
            remediation="Use letters, digits, dots, dashes, and underscores; no directory parts.",
        )
    base = Path(base_dir).resolve()
    joined = base.joinpath(*parts).resolve()
    if not joined.is_relative_to(base):
        raise ValidationError(
            f"Path {leaf!r} escapes the profiles directory.",
            remediation="Keep profile artifacts inside the profiles directory.",
        )
    return str(joined)


@dataclass(frozen=True)
class IngestionProfile:
    """One validated profile, resolved to plain values."""

    name: str
    data_type: str
    description: str = ""
    strategies: dict[str, str] = field(default_factory=dict)
    default_strategy: str | None = None
    extraction_mode: str | None = None
    validate_mode: str = "off"
    seeds_file: str | None = None
    templates: tuple[str, ...] = ()
    calibrate: CalibrateSpec | None = None


def profiles_dir(config: dict[str, Any]) -> str:
    """The profiles directory (``profiles.dir``; default ``profiles``)."""
    raw = config.get("profiles")
    typed = raw if isinstance(raw, dict) else {}
    value = typed.get("dir")
    return value if isinstance(value, str) and value else DEFAULT_PROFILES_DIR


def resolve_profile_name(config: dict[str, Any]) -> str | None:
    """The active profile name (``ingestion.profile``), if any."""
    ingestion = config.get("ingestion")
    typed = ingestion if isinstance(ingestion, dict) else {}
    raw = typed.get("profile")
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw:
        raise ValidationError(
            "ingestion.profile must be a profile name string.",
            remediation="Point ingestion.profile at a profile file, or remove the key.",
        )
    return raw


def load_profile(config: dict[str, Any], override: str | None = None) -> IngestionProfile | None:
    """Load the active profile, or None when no profile is configured.

    ``override`` (an explicit per-call name) wins over the config value.
    A missing file or a bad value raises ValidationError — a configured
    profile is a contract, not a preference.
    """
    name = override or resolve_profile_name(config)
    if name is None:
        return None
    path = safe_profile_path(profiles_dir(config), f"{name}.yaml")
    profile_file = Path(path)
    if not profile_file.exists():
        raise ValidationError(
            f"Profile {name!r} not found at {path}.",
            remediation="Create the profile file, or point ingestion.profile at an existing one.",
        )
    raw = yaml.safe_load(profile_file.read_text(encoding="utf-8"))
    return parse_profile(raw, name, path)


def parse_profile(raw: Any, name: str, path: str) -> IngestionProfile:
    """Validate one raw profile mapping into an IngestionProfile."""
    if not isinstance(raw, dict):
        raise ValidationError(
            f"Profile {name!r} must be a YAML mapping.",
            remediation="Start the file with 'name: {name}'.",
        )
    unknown = sorted(set(raw) - KNOWN_TOP_KEYS)
    if unknown:
        raise ValidationError(
            f"Profile {name!r} has unknown keys: {', '.join(unknown)}.",
            remediation=f"Allowed keys: {', '.join(sorted(KNOWN_TOP_KEYS))}.",
        )
    file_name = raw.get("name")
    if file_name != name:
        raise ValidationError(
            f"Profile file {path} declares name {file_name!r}, expected {name!r}.",
            remediation="Make the 'name' value match the file name without the .yaml suffix.",
        )
    data_type = raw.get("data_type", "mixed")
    if data_type not in DATA_TYPES:
        raise ValidationError(
            f"Profile {name!r} has unknown data_type {data_type!r}.",
            remediation=f"Use one of: {', '.join(sorted(DATA_TYPES))}.",
        )
    strategies, default_strategy = parse_ingestion(raw.get("ingestion"), name)
    mode = parse_extraction(raw.get("extraction"), name)
    validate_mode = raw.get("validate", "off")
    if isinstance(validate_mode, bool) or validate_mode not in PROFILE_VALIDATE_MODES:
        raise ValidationError(
            f"Profile {name!r} has unknown validate mode {validate_mode!r}.",
            remediation=(
                "Use one of: off, advisory, enforce, calibrate, transcript_meta."
                " YAML reads a bare off/on as true/false, so quote it: validate: 'off'."
            ),
        )
    templates = raw.get("templates", [])
    if not isinstance(templates, list) or not all(isinstance(t, str) for t in templates):
        raise ValidationError(
            f"Profile {name!r} templates must be a list of template ids.",
            remediation="List claim-template ids, e.g. [preference, opinion].",
        )
    seeds_file = raw.get("seeds_file")
    if seeds_file is not None and (not isinstance(seeds_file, str) or not seeds_file):
        raise ValidationError(
            f"Profile {name!r} seeds_file must be a file name.",
            remediation="Point seeds_file at a YAML file in the profiles directory.",
        )
    description = raw.get("description", "")
    return IngestionProfile(
        name=name,
        data_type=data_type,
        description=description if isinstance(description, str) else "",
        strategies=strategies,
        default_strategy=default_strategy,
        extraction_mode=mode,
        validate_mode=validate_mode,
        seeds_file=seeds_file,
        templates=tuple(templates),
        calibrate=parse_calibrate(raw.get("calibrate"), name),
    )


def with_profile_overrides(
    config: dict[str, Any], profile: IngestionProfile | None
) -> dict[str, Any]:
    """One config copy with the profile's extraction settings applied.

    Ingestion-strategy resolution reads the profile object directly in
    the registry (precedence: explicit > profile > config map), so only
    the extraction block needs a merged copy here. The input config is
    never mutated.
    """
    if profile is None or profile.extraction_mode is None:
        return config
    extraction = config.get("extraction")
    merged = dict(extraction) if isinstance(extraction, dict) else {}
    merged["mode"] = profile.extraction_mode
    return {**config, "extraction": merged}
