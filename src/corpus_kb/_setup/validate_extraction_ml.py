"""Validation of the ``extraction.mode``/``relex``/``ensemble``/``cache``
config keys (the ``ml`` extraction path). Split out of
``validate_configs.py`` when the block outgrew the file complexity gate."""

from __future__ import annotations

from typing import Any

# Backends an extraction.ensemble member may name (extraction/__init__.py
# EXTRACTION_BACKENDS minus the composite itself, which is always first).
VALID_BACKENDS: frozenset[str] = frozenset({"regex", "langextract", "pgml", "gliner"})


def validate_extraction_ml(config: dict[str, Any]) -> list[str]:
    """Validate the ``extraction.mode``/``relex``/``ensemble``/``cache`` keys.

    Every key is checked so a typo can never silently disable the default
    ``ml`` extraction path (mode gate, relex model block, ensemble member
    names, cache flag).

    Returns a list of error strings (empty = valid).
    """
    errors: list[str] = []

    extraction = config.get("extraction")
    if extraction is None:
        return errors
    if not isinstance(extraction, dict):
        return [f"extraction: must be a mapping, got {type(extraction).__name__}"]

    _mode_errors(extraction, errors)
    _relex_errors(extraction, errors)
    _ensemble_errors(extraction, errors)
    _cache_errors(extraction, errors)
    return errors


def _mode_errors(extraction: dict[str, Any], errors: list[str]) -> None:
    mode = extraction.get("mode")
    if mode is not None and mode not in ("zero_shot", "few_shot", "customized"):
        errors.append(
            f"extraction.mode: must be one of zero_shot, few_shot, customized — got {mode!r}"
        )


def _relex_errors(extraction: dict[str, Any], errors: list[str]) -> None:
    relex = extraction.get("relex")
    if relex is None:
        return
    if not isinstance(relex, dict):
        errors.append(f"extraction.relex: must be a mapping, got {type(relex).__name__}")
        return
    model = relex.get("model")
    if not isinstance(model, str) or not model:
        errors.append("extraction.relex.model: must be a non-empty string")
    threshold = relex.get("threshold")
    if threshold is not None and (
        not isinstance(threshold, (int, float)) or not 0.0 < threshold <= 1.0
    ):
        errors.append("extraction.relex.threshold: must be a number in (0, 1]")
    max_pairs = relex.get("max_label_pairs")
    if max_pairs is not None and (not isinstance(max_pairs, int) or max_pairs < 1):
        errors.append("extraction.relex.max_label_pairs: must be an integer >= 1")


def _ensemble_errors(extraction: dict[str, Any], errors: list[str]) -> None:
    ensemble = extraction.get("ensemble")
    if ensemble is None:
        return
    if not isinstance(ensemble, dict):
        errors.append(f"extraction.ensemble: must be a mapping, got {type(ensemble).__name__}")
        return
    enabled = ensemble.get("enabled")
    if not isinstance(enabled, bool):
        errors.append(
            f"extraction.ensemble.enabled: must be a boolean, got {type(enabled).__name__}"
        )
    members = ensemble.get("members")
    if members is None:
        return
    if not isinstance(members, list) or not members:
        errors.append("extraction.ensemble.members: must be a non-empty list")
        return
    for member in members:
        if not isinstance(member, str) or member not in VALID_BACKENDS:
            errors.append(
                f"extraction.ensemble.members: unknown backend {member!r} — "
                f"must be one of: {', '.join(sorted(VALID_BACKENDS))}"
            )


def _cache_errors(extraction: dict[str, Any], errors: list[str]) -> None:
    cache = extraction.get("cache")
    if cache is None:
        return
    if not isinstance(cache, dict):
        errors.append(f"extraction.cache: must be a mapping, got {type(cache).__name__}")
        return
    enabled = cache.get("enabled")
    if not isinstance(enabled, bool):
        errors.append(f"extraction.cache.enabled: must be a boolean, got {type(enabled).__name__}")
