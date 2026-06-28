"""Ontology loader and model for Corpus-KB.

An ontology defines the vocabulary of entity and relation types that the
extractor is allowed to emit. Loaded from a YAML artifact.
"""

from __future__ import annotations

from pathlib import Path
from typing import Self, cast

import yaml
from pydantic import BaseModel, ValidationInfo, field_validator, model_validator


class Ontology(BaseModel):
    """Ontology vocabulary governing extraction output."""

    entity_types: list[str]
    relation_types: list[str]

    @field_validator("entity_types", "relation_types")
    @classmethod
    def _non_empty_unique(
        cls, values: list[str], info: ValidationInfo
    ) -> list[str]:
        if not values:
            raise ValueError(f"{info.field_name} must be non-empty")
        if len(set(values)) != len(values):
            raise ValueError(f"{info.field_name} must contain unique values")
        return values

    @model_validator(mode="after")
    def _disjoint(self) -> Self:
        overlap = set(self.entity_types) & set(self.relation_types)
        if overlap:
            raise ValueError(
                "entity_types and relation_types must be disjoint; "
                f"overlap: {overlap}"
            )
        return self


def load_ontology(path: str | Path) -> Ontology:
    """Load and validate an ontology from a YAML file.

    Args:
        path: Path to the YAML ontology file.

    Returns:
        Validated Ontology instance.

    Raises:
        ValueError: If the file is missing required keys or malformed.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise ValueError(f"Ontology file not found: {path}")

    with open(file_path) as f:
        raw: object = yaml.safe_load(f) or {}

    if not isinstance(raw, dict):
        raise ValueError(f"Ontology file must contain a YAML mapping: {path}")

    data = cast(dict[str, object], raw)
    try:
        return Ontology.model_validate(data)
    except Exception as exc:
        raise ValueError(f"Malformed ontology file {path}: {exc}") from exc
