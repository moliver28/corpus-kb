"""Project ingestion profiles (Living Corpus W5).

A profile is one authored YAML file under ``profiles/`` that turns a
project corpus into a paved pipeline: ingestion strategy mapping,
extraction mode, validation mode, claim templates, and tuning seeds —
zero code for the end user. The harness authors profiles;
``validate_profiles`` gates them; ``run_pipeline`` resolves them.
"""

from __future__ import annotations

from corpus_kb.profiles.loader import (
    IngestionProfile,
    load_profile,
    load_seeds,
    profile_seeds_path,
    profiles_dir,
    resolve_profile_name,
    safe_profile_path,
    with_profile_overrides,
)

__all__ = [
    "IngestionProfile",
    "load_profile",
    "load_seeds",
    "profile_seeds_path",
    "profiles_dir",
    "resolve_profile_name",
    "safe_profile_path",
    "with_profile_overrides",
]
