"""Claim template catalogue (Living Corpus W2).

Templates are validated YAML files (``config/claim_templates/*.yaml``).
Each template names ONE claim shape end users recognize — preference,
opinion, sentiment toward a target, a fact triple, a numeric claim, a
temporal change, an attribution — and carries the deterministic signals
that classify a sentence into it, plus the validation rules a candidate
statement must pass. End users never write code: a new claim shape is a
new YAML file, picked up by the one loader and checked by the one
validator (``poe verify`` runs it).
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

TEMPLATE_VERSION = "v1"


class TemplateSpec(BaseModel):
    """One validated claim shape."""

    model_config = {"extra": "forbid"}

    id: str
    description: str
    version: str = TEMPLATE_VERSION
    # Case-insensitive regexes; a sentence matching any signal classifies.
    signals: list[str] = Field(default_factory=list)
    # A candidate must contain at least this many anchored entities.
    min_entities: int = 0
    # Role slots the claim shape fills (for the store envelope + docs).
    roles: list[str] = Field(default_factory=list)


class TemplateCatalogue:
    """The loaded template set with its compiled signals.

    The built-in ``statement`` fallback shape is always present: it
    catches every sentence no shipped template claims, so atomization
    never drops a sentence silently.
    """

    FALLBACK = TemplateSpec(
        id="statement",
        description="A plain factual assertion no specialized claim shape matches.",
        signals=[],
        min_entities=0,
        roles=[],
    )

    def __init__(self, templates: list[TemplateSpec]) -> None:
        if not templates:
            raise ValueError("claim template catalogue is empty")
        ids = [t.id for t in templates]
        if len(set(ids)) != len(ids):
            raise ValueError(f"duplicate claim template ids: {ids}")
        if "statement" not in ids:
            templates = [*templates, self.FALLBACK]
        self._templates = {t.id: t for t in templates}
        self._compiled = {
            t.id: [re.compile(p, re.IGNORECASE) for p in t.signals] for t in templates
        }

    def ids(self) -> list[str]:
        return sorted(self._templates)

    def spec(self, template_id: str) -> TemplateSpec:
        return self._templates[template_id]

    def classify(self, text: str) -> str:
        """First template (catalogue order) whose signal matches; else the
        built-in ``statement`` fallback shape."""
        for template_id, patterns in self._compiled.items():
            if any(p.search(text) for p in patterns):
                return template_id
        return "statement"

    def validate_candidate(self, template_id: str, entity_count: int) -> bool:
        spec = self._templates[template_id]
        return entity_count >= spec.min_entities


def load_templates(directory: str | Path) -> TemplateCatalogue:
    """Load and validate every ``*.yaml`` template in the directory.

    A malformed file raises immediately — a broken template must fail the
    gate, never silently disable a claim shape.
    """
    root = Path(directory)
    if not root.is_dir():
        raise FileNotFoundError(f"claim template directory not found: {root}")
    templates: list[TemplateSpec] = []
    for path in sorted(root.glob("*.yaml")):
        import yaml

        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"{path.name}: template must be a mapping")
        templates.append(TemplateSpec.model_validate(raw))
    return TemplateCatalogue(templates)
