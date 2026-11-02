"""Block validators for the runtime ``config.yaml``.

Covers the opt-in blocks whose when-present rules go beyond the plain
dotted-key type map (``validate_configs_keys.py``):
``extraction.gliner``, ``extraction.ml`` (delegated), ``statements``, and
``ingestion`` — plus the shared YAML loader.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

try:
    # Package context (installed checkout, tests).
    from corpus_kb._setup.validate_extraction_ml import (
        validate_extraction_ml as _validate_extraction_ml,
    )
except ImportError:  # pragma: no cover - script context (CI validate job)
    import importlib as _importlib

    _validate_extraction_ml = _importlib.import_module(
        "validate_extraction_ml"
    ).validate_extraction_ml

# The six mention-level types gliner_backend.py can emit (must stay in
# lockstep with extraction.gliner_backend.TYPE_DEFINITIONS).
GLINER_MENTION_TYPES: frozenset[str] = frozenset(
    {"person", "organization", "location", "date", "money", "product"}
)


def validate_statements_block(config: dict[str, Any]) -> list[str]:
    """Validate the ``statements`` block (Living Corpus W2 atomization)."""
    errors: list[str] = []
    statements = config.get("statements")
    if statements is None:
        return errors
    if not isinstance(statements, dict):
        return [f"statements: must be a mapping, got {type(statements).__name__}"]
    enabled = statements.get("enabled")
    if not isinstance(enabled, bool):
        errors.append(f"statements.enabled: must be a boolean, got {type(enabled).__name__}")
    templates_dir = statements.get("templates_dir")
    if templates_dir is not None and not isinstance(templates_dir, str):
        errors.append("statements.templates_dir: must be a string path")
    max_per_chunk = statements.get("max_per_chunk")
    if max_per_chunk is not None and (not isinstance(max_per_chunk, int) or max_per_chunk < 1):
        errors.append("statements.max_per_chunk: must be an integer >= 1")
    return errors


def load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file, returning {} for empty or non-mapping documents."""
    with path.open(encoding="utf-8") as f:
        loaded: object = yaml.safe_load(f)
    return loaded if isinstance(loaded, dict) else {}


def validate_extraction_gliner(config: dict[str, Any]) -> list[str]:
    """Validate the ``extraction.gliner`` block of a runtime config.

    The block (and the whole ``extraction`` key) is optional; when
    present, every key is checked so a typo can never silently disable
    the opt-in GLiNER2 backend.

    Returns a list of error strings (empty = valid).
    """
    errors: list[str] = []

    extraction = config.get("extraction")
    if extraction is None:
        return errors
    if not isinstance(extraction, dict):
        return [f"extraction: must be a mapping, got {type(extraction).__name__}"]

    gliner = extraction.get("gliner")
    if gliner is None:
        return errors
    if not isinstance(gliner, dict):
        return [f"extraction.gliner: must be a mapping, got {type(gliner).__name__}"]

    enabled = gliner.get("enabled")
    if not isinstance(enabled, bool):
        errors.append(f"extraction.gliner.enabled: must be a boolean, got {type(enabled).__name__}")

    model = gliner.get("model")
    if not isinstance(model, str) or not model:
        errors.append("extraction.gliner.model: must be a non-empty string")

    revision = gliner.get("revision")
    if not isinstance(revision, str) or not revision:
        errors.append("extraction.gliner.revision: must be a non-empty string")

    types = gliner.get("types")
    if not isinstance(types, list) or not types:
        errors.append("extraction.gliner.types: must be a non-empty list")
    else:
        for mention_type in types:
            if not isinstance(mention_type, str) or mention_type not in GLINER_MENTION_TYPES:
                errors.append(
                    f"extraction.gliner.types: unknown mention type {mention_type!r} — "
                    f"must be one of: {', '.join(sorted(GLINER_MENTION_TYPES))}"
                )

    return errors


def validate_extraction_ml(config: dict[str, Any]) -> list[str]:
    """Validate the ``extraction.mode``/``relex``/``ensemble``/``cache`` keys.

    Thin re-export: the checks live in ``validate_extraction_ml.py`` (the
    block outgrew this module's complexity gate). Kept importable from here
    for the historical ``validate_configs.<name>`` surface.
    """
    return _validate_extraction_ml(config)


def validate_ingestion_block(config: dict[str, Any]) -> list[str]:
    """Validate the ``ingestion`` block of a runtime config.

    The block is optional; when present, ``strategies`` must map source
    types to non-empty strategy-name strings and ``default`` must be a
    non-empty string — the registry resolves these names at ingest time, so
    a mistyped value must fail validation instead of silently falling back.

    Returns a list of error strings (empty = valid).
    """
    errors: list[str] = []

    ingestion = config.get("ingestion")
    if ingestion is None:
        return errors
    if not isinstance(ingestion, dict):
        return [f"ingestion: must be a mapping, got {type(ingestion).__name__}"]

    strategies = ingestion.get("strategies")
    if not isinstance(strategies, dict):
        errors.append(
            f"ingestion.strategies: must be a mapping of source type -> strategy name, "
            f"got {type(strategies).__name__}"
        )
    else:
        for source_type, name in strategies.items():
            if not isinstance(source_type, str) or not source_type:
                errors.append(
                    f"ingestion.strategies: keys must be non-empty source-type strings, "
                    f"got {source_type!r}"
                )
            if not isinstance(name, str) or not name:
                errors.append(
                    f"ingestion.strategies[{source_type!r}]: must be a non-empty "
                    f"strategy-name string, got {name!r}"
                )

    default = ingestion.get("default")
    if not isinstance(default, str) or not default:
        errors.append("ingestion.default: must be a non-empty string")

    tokenizer = ingestion.get("tokenizer")
    if tokenizer is not None and tokenizer != "semchunk":
        errors.append(
            f"ingestion.tokenizer: unknown value {tokenizer!r} — must be 'semchunk' "
            "(the key stays absent for the default chars/4 split)"
        )

    dedup = ingestion.get("dedup")
    if dedup is not None and dedup != "minhash":
        errors.append(
            f"ingestion.dedup: unknown value {dedup!r} — must be 'minhash' "
            "(the key stays absent when dedup is off)"
        )

    threshold = ingestion.get("dedup_threshold")
    if threshold is not None:
        if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
            errors.append("ingestion.dedup_threshold: must be a number")
        elif not 0.0 < float(threshold) <= 1.0:
            errors.append("ingestion.dedup_threshold: must be in (0, 1]")

    return errors
