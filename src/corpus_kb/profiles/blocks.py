"""Parsers for the ``ingestion:`` and ``extraction:`` blocks of a profile.

Each parser validates one YAML sub-block and returns plain values.
Unknown keys and unknown enum values fail loudly — the house pattern:
never a silent fallback.
"""

from __future__ import annotations

from typing import Any

from ..errors import ValidationError

# Mirrors extraction.ml_backend.EXTRACTION_MODES (kept literal so profiles
# never imports extraction; test_profiles pins the two in lockstep).
PROFILE_EXTRACTION_MODES: frozenset[str] = frozenset({"zero_shot", "few_shot", "customized"})
KNOWN_INGESTION_KEYS: frozenset[str] = frozenset({"strategies", "default"})
KNOWN_EXTRACTION_KEYS: frozenset[str] = frozenset({"mode"})


def parse_ingestion(raw: Any, name: str) -> tuple[dict[str, str], str | None]:
    if raw is None:
        return {}, None
    if not isinstance(raw, dict):
        raise ValidationError(
            f"Profile {name!r} ingestion must be a mapping.",
            remediation="Use 'strategies' (source_type to strategy) and optional 'default'.",
        )
    unknown = sorted(set(raw) - KNOWN_INGESTION_KEYS)
    if unknown:
        raise ValidationError(
            f"Profile {name!r} ingestion has unknown keys: {', '.join(unknown)}.",
            remediation=f"Allowed keys: {', '.join(sorted(KNOWN_INGESTION_KEYS))}.",
        )
    raw_strategies = raw.get("strategies", {})
    if not isinstance(raw_strategies, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in raw_strategies.items()
    ):
        raise ValidationError(
            f"Profile {name!r} ingestion.strategies must map source types to strategy names.",
            remediation="Example: strategies: {transcript: transcript}.",
        )
    default = raw.get("default")
    if default is not None and (not isinstance(default, str) or not default):
        raise ValidationError(
            f"Profile {name!r} ingestion.default must be a strategy name.",
            remediation="Example: default: transcript.",
        )
    return dict(raw_strategies), default


def parse_extraction(raw: Any, name: str) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValidationError(
            f"Profile {name!r} extraction must be a mapping.",
            remediation="Use 'mode: zero_shot | few_shot | customized'.",
        )
    unknown = sorted(set(raw) - KNOWN_EXTRACTION_KEYS)
    if unknown:
        raise ValidationError(
            f"Profile {name!r} extraction has unknown keys: {', '.join(unknown)}.",
            remediation=f"Allowed keys: {', '.join(sorted(KNOWN_EXTRACTION_KEYS))}.",
        )
    mode = raw.get("mode")
    if mode is not None and mode not in PROFILE_EXTRACTION_MODES:
        raise ValidationError(
            f"Profile {name!r} has unknown extraction mode {mode!r}.",
            remediation=f"Use one of: {', '.join(sorted(PROFILE_EXTRACTION_MODES))}.",
        )
    return mode if isinstance(mode, str) else None
