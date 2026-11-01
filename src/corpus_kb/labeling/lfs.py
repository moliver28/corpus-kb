"""Labeling-function definitions: the JSON file schema and its loader.

``analysis.labeling.lfs_file`` points at one JSON file:

    {
      "classes": ["relevant", "noise"],
      "lfs": [
        {"name": "mentions-sla", "kind": "regex", "label": "relevant",
         "config": {"pattern": "\\bSLA\\b"}}
      ]
    }

The kinds and their ``config`` keys are documented in ``kinds.py``. An
unknown kind, an unknown class name, or an invalid config is a
``ValidationError`` AT LOAD with the fix in the message — a broken LF
file never fails silently mid-build. Duplicate LF names are rejected
too. A file with zero LFs loads fine; the caller owns the honest
empty-run decision.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from corpus_kb.errors import ValidationError

from .kinds import SUPPORTED_KINDS, compile_pattern
from .protocol import LabelingFunction

__all__ = ["SUPPORTED_KINDS", "LFSpec", "load_lfs"]


@dataclass(frozen=True)
class LFSpec:
    """One parsed LF file: the class vocabulary plus the functions."""

    classes: tuple[str, ...]
    lfs: tuple[LabelingFunction, ...]


def load_lfs(path: str) -> LFSpec:
    """Parse and validate one LF definitions file (fail fast, with fixes).

    Raises ``ValidationError`` with the remediation in the message for
    invalid JSON, an unknown kind or class, a duplicate LF name, or an
    invalid config.
    """
    try:
        raw: object = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(
            f"LF file {path} is not valid JSON: {exc.msg} (line {exc.lineno})."
        ) from exc
    if not isinstance(raw, dict):
        raise ValidationError(f"LF file {path} must hold a JSON object with classes and lfs.")
    classes = _parse_classes(path, raw.get("classes"))
    raw_lfs = raw.get("lfs")
    if not isinstance(raw_lfs, list):
        raise ValidationError(f'LF file {path}: the "lfs" key must hold a list.')
    lfs: list[LabelingFunction] = []
    names: set[str] = set()
    for entry in raw_lfs:
        lf = _parse_lf(path, entry, classes)
        if lf.name in names:
            raise ValidationError(f'LF file {path}: duplicate LF name "{lf.name}".')
        names.add(lf.name)
        lfs.append(lf)
    return LFSpec(classes=classes, lfs=tuple(lfs))


def _parse_classes(path: str, raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        raise ValidationError(f'LF file {path}: "classes" must be a non-empty list of strings.')
    classes: list[str] = []
    for name in raw:
        if not isinstance(name, str) or not name.strip():
            raise ValidationError(f"LF file {path}: every class name must be a non-empty string.")
        if name in classes:
            raise ValidationError(f'LF file {path}: duplicate class name "{name}".')
        classes.append(name)
    return tuple(classes)


def _parse_lf(path: str, raw: object, classes: tuple[str, ...]) -> LabelingFunction:
    if not isinstance(raw, dict):
        raise ValidationError(f'LF file {path}: every entry in "lfs" must be a JSON object.')
    name = raw.get("name")
    kind = raw.get("kind")
    label = raw.get("label")
    config = raw.get("config", {})
    if not isinstance(name, str) or not name.strip():
        raise ValidationError(f'LF file {path}: every LF needs a non-empty "name".')
    if kind not in SUPPORTED_KINDS:
        supported = ", ".join(sorted(SUPPORTED_KINDS))
        raise ValidationError(
            f'LF file {path}: LF "{name}" has unknown kind {kind!r}. Supported kinds: {supported}.'
        )
    if not isinstance(label, str) or label not in classes:
        raise ValidationError(
            f'LF file {path}: LF "{name}" votes for unknown class {label!r}. '
            f'Add it to "classes" or use one of: {", ".join(classes)}.'
        )
    if not isinstance(config, dict):
        raise ValidationError(f'LF file {path}: LF "{name}" config must be a JSON object.')
    cleaned = _CONFIG_VALIDATORS[kind](name, config)
    return LabelingFunction(
        name=name, kind=kind, label=label, label_index=classes.index(label), config=cleaned
    )


# ---------------------------------------------------------------------------
# Per-kind config validation (strict: unknown keys are typos, reject them).
# ---------------------------------------------------------------------------


def _pattern_config(lf_name: str, config: Mapping[str, object]) -> dict[str, object]:
    _reject_unknown_keys(lf_name, config, {"pattern"})
    pattern = _required_str(lf_name, config, "pattern")
    _compile_checked(lf_name, pattern)
    return {"pattern": pattern}


def _lexicon_config(lf_name: str, config: Mapping[str, object]) -> dict[str, object]:
    _reject_unknown_keys(lf_name, config, {"terms", "case_sensitive"})
    raw_terms = config.get("terms")
    if not isinstance(raw_terms, list) or not raw_terms:
        raise ValidationError(
            f"LF {lf_name!r}: config.terms must be a non-empty list of strings. "
            'Example: "terms": ["sla", "uptime"].'
        )
    for term in raw_terms:
        if not isinstance(term, str) or not term.strip():
            raise ValidationError(f"LF {lf_name!r}: every term must be a non-empty string.")
    case_sensitive = config.get("case_sensitive", False)
    if not isinstance(case_sensitive, bool):
        raise ValidationError(
            f"LF {lf_name!r}: config.case_sensitive must be true or false; got {case_sensitive!r}."
        )
    return {"terms": tuple(str(term) for term in raw_terms), "case_sensitive": case_sensitive}


def _negation_config(lf_name: str, config: Mapping[str, object]) -> dict[str, object]:
    _reject_unknown_keys(lf_name, config, {"pattern", "negation_window"})
    pattern = _required_str(lf_name, config, "pattern")
    _compile_checked(lf_name, pattern)
    window = config.get("negation_window", 40)
    if isinstance(window, bool) or not isinstance(window, int) or window <= 0:
        raise ValidationError(
            f"LF {lf_name!r}: config.negation_window must be a positive int "
            f"(characters to scan before a match); got {window!r}."
        )
    return {"pattern": pattern, "negation_window": window}


def _compile_checked(lf_name: str, pattern: str) -> None:
    """Compile one pattern; an invalid regex is a plain-English ValidationError."""
    try:
        compile_pattern(pattern, 0)
    except re.error as exc:
        raise ValidationError(
            f"LF {lf_name!r}: config.pattern is not a valid regex: {exc}."
        ) from exc


def _metadata_config(lf_name: str, config: Mapping[str, object]) -> dict[str, object]:
    _reject_unknown_keys(lf_name, config, {"key", "equals"})
    key = _required_str(lf_name, config, "key")
    equals = _required_str(lf_name, config, "equals")
    return {"key": key, "equals": equals}


def _required_str(lf_name: str, config: Mapping[str, object], key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(
            f"LF {lf_name!r}: config.{key} must be a non-empty string; got {value!r}."
        )
    return value


def _reject_unknown_keys(lf_name: str, config: Mapping[str, object], known: set[str]) -> None:
    unknown = sorted(set(config) - known)
    if unknown:
        allowed = ", ".join(sorted(known))
        raise ValidationError(
            f"LF {lf_name!r}: unknown config keys {unknown}. Allowed keys: {allowed}."
        )


_CONFIG_VALIDATORS: dict[str, Callable[[str, Mapping[str, object]], dict[str, object]]] = {
    "regex": _pattern_config,
    "lexicon": _lexicon_config,
    "negation_regex": _negation_config,
    "metadata": _metadata_config,
}
