"""Ontology loader and model for Corpus-KB.

An ontology defines the vocabulary of entity and relation types that the
extractor is allowed to emit. Loaded from a YAML artifact.

Optional enrichment (all tolerated as absent): a top-level ``version``
string, and per-type ``description`` entries. Each type entry may be a
plain string (``- Person``) or a mapping (``- {name: Person,
description: A human individual}``); descriptions of mapped entries
land in ``entity_descriptions``/``relation_descriptions`` and the
existing ``entity_types``/``relation_types`` lists keep containing plain
names, so every downstream consumer is unaffected.
"""

from __future__ import annotations

from pathlib import Path
from typing import Self, cast

import yaml
from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator


class Ontology(BaseModel):
    """Ontology vocabulary governing extraction output."""

    entity_types: list[str]
    relation_types: list[str]
    version: str | None = None
    entity_descriptions: dict[str, str] = Field(default_factory=dict)
    relation_descriptions: dict[str, str] = Field(default_factory=dict)

    @field_validator("entity_types", "relation_types")
    @classmethod
    def _non_empty_unique(cls, values: list[str], info: ValidationInfo) -> list[str]:
        if not values:
            raise ValueError(f"{info.field_name} must be non-empty")
        if len(set(values)) != len(values):
            raise ValueError(f"{info.field_name} must contain unique values")
        return values

    @field_validator("entity_descriptions")
    @classmethod
    def _entity_descriptions_declared(
        cls, descriptions: dict[str, str], info: ValidationInfo
    ) -> dict[str, str]:
        return cls._check_declared(descriptions, info.data, "entity_types")

    @field_validator("relation_descriptions")
    @classmethod
    def _relation_descriptions_declared(
        cls, descriptions: dict[str, str], info: ValidationInfo
    ) -> dict[str, str]:
        return cls._check_declared(descriptions, info.data, "relation_types")

    @staticmethod
    def _check_declared(
        descriptions: dict[str, str], data: dict[str, object], types_key: str
    ) -> dict[str, str]:
        types = data.get(types_key)
        if isinstance(types, list):
            undeclared = set(descriptions) - set(types)
            if undeclared:
                raise ValueError(
                    f"{types_key.replace('_types', '_descriptions')} reference types not "
                    f"declared in {types_key}: {sorted(undeclared)}"
                )
        return descriptions

    @model_validator(mode="after")
    def _disjoint(self) -> Self:
        overlap = set(self.entity_types) & set(self.relation_types)
        if overlap:
            raise ValueError(
                f"entity_types and relation_types must be disjoint; overlap: {overlap}"
            )
        return self


def _normalize_type_entries(raw: object) -> tuple[list[str], dict[str, str]]:
    """Normalize a type list that mixes plain strings and typed mappings.

    Plain strings pass through unchanged. ``{name, description}`` mappings
    contribute their name plus description. Anything else is left in place
    so the Pydantic validators reject it loudly instead of it being
    silently dropped.
    """
    names: list[str] = []
    descriptions: dict[str, str] = {}
    if not isinstance(raw, list):
        return names, descriptions
    for entry in raw:
        if isinstance(entry, str):
            names.append(entry)
            continue
        if isinstance(entry, dict):
            name = entry.get("name")
            description = entry.get("description")
            if isinstance(name, str):
                names.append(name)
                if isinstance(description, str):
                    descriptions[name] = description
                continue
        # Unrecognized shape: keep it so the Pydantic validators reject it
        # loudly instead of it being silently dropped.
        names.append(cast(str, entry))
    return names, descriptions


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

    with file_path.open() as f:
        raw: object = yaml.safe_load(f) or {}

    if not isinstance(raw, dict):
        raise ValueError(f"Ontology file must contain a YAML mapping: {path}")

    data = cast(dict[str, object], raw)
    entity_types, entity_descriptions = _normalize_type_entries(data.get("entity_types"))
    relation_types, relation_descriptions = _normalize_type_entries(data.get("relation_types"))
    data = dict(data)
    data["entity_types"] = entity_types
    data["relation_types"] = relation_types
    data.setdefault("entity_descriptions", entity_descriptions)
    data.setdefault("relation_descriptions", relation_descriptions)
    try:
        return Ontology.model_validate(data)
    except Exception as exc:
        raise ValueError(f"Malformed ontology file {path}: {exc}") from exc
