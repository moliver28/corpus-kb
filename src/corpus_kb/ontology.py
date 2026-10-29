"""Ontology loader and model for Corpus-KB.

An ontology defines the vocabulary of entity and relation types that the
extractor is allowed to emit. Loaded from a YAML artifact.

Optional enrichment (all tolerated as absent): a top-level ``version``
string, per-type ``description`` entries, and — relation types only — a
per-type ``graph_eligible`` boolean. Each type entry may be a plain
string (``- Person``) or a mapping (``- {name: Person,
description: A human individual}``); descriptions of mapped entries
land in ``entity_descriptions``/``relation_descriptions`` and the
existing ``entity_types``/``relation_types`` lists keep containing plain
names, so every downstream consumer is unaffected. An explicit
``graph_eligible`` on a relation mapping lands in
``relation_graph_eligible``; every type absent from that map (and every
plain-string entry) is graph-eligible, so the shipped vocabulary keeps
today's write behavior unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Self, TypeVar, cast

import yaml
from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

_T = TypeVar("_T")


class Ontology(BaseModel):
    """Ontology vocabulary governing extraction output."""

    entity_types: list[str]
    relation_types: list[str]
    version: str | None = None
    entity_descriptions: dict[str, str] = Field(default_factory=dict)
    relation_descriptions: dict[str, str] = Field(default_factory=dict)
    # Explicit per-relation graph-write flags only; absent = eligible
    # (graph_policy.mode "all" stays byte-identical to the pre-flag reads).
    relation_graph_eligible: dict[str, bool] = Field(default_factory=dict)

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

    @field_validator("relation_graph_eligible")
    @classmethod
    def _graph_flags_declared(cls, flags: dict[str, bool], info: ValidationInfo) -> dict[str, bool]:
        return cls._check_declared(flags, info.data, "relation_types")

    def relation_is_graph_eligible(self, relation_type: str) -> bool:
        """The graph-write verdict for one relation type; absent = eligible."""
        return self.relation_graph_eligible.get(relation_type, True)

    @staticmethod
    def _check_declared(
        entries: dict[str, _T], data: dict[str, object], types_key: str
    ) -> dict[str, _T]:
        types = data.get(types_key)
        if isinstance(types, list):
            undeclared = set(entries) - set(types)
            if undeclared:
                raise ValueError(
                    f"{types_key.replace('_types', '_descriptions')} reference types not "
                    f"declared in {types_key}: {sorted(undeclared)}"
                )
        return entries

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


def _extract_graph_flags(raw: object) -> dict[str, bool]:
    """The explicit ``graph_eligible`` flags on relation mapping entries.

    Plain-string entries contribute nothing (absent = eligible). A flag on
    a mapping entry without a usable name is ignored with the entry; a
    non-boolean flag is rejected loudly instead of being dropped.
    """
    flags: dict[str, bool] = {}
    if not isinstance(raw, list):
        return flags
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        flag = entry.get("graph_eligible")
        if flag is None:
            continue
        if not isinstance(flag, bool):
            raise ValueError(f"graph_eligible must be true or false, got {flag!r}")
        name = entry.get("name")
        if isinstance(name, str):
            flags[name] = flag
    return flags


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
    raw_relations = data.get("relation_types")
    relation_types, relation_descriptions = _normalize_type_entries(raw_relations)
    data = dict(data)
    data["entity_types"] = entity_types
    data["relation_types"] = relation_types
    data.setdefault("entity_descriptions", entity_descriptions)
    data.setdefault("relation_descriptions", relation_descriptions)
    try:
        data.setdefault("relation_graph_eligible", _extract_graph_flags(raw_relations))
        return Ontology.model_validate(data)
    except Exception as exc:
        raise ValueError(f"Malformed ontology file {path}: {exc}") from exc
