"""The analysis method register: 30 NLP methods as validated data.

The register YAML (``analysis.method_register_path``, default
``config/analysis_methods.yaml``) is the single source of truth. It
carries the owner's research document as data: identity, ordinal, and
descriptive axes per method. Descriptive axes are surfaced verbatim;
they are never scored (the document forbids invented ratings).

The selector rubric lives in ``method_register_rubric``. Import
``rank_methods`` from there; this module holds the models and the
loader. The wave-2 handler and the UI both call the one rubric.
"""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, field_validator, model_validator

from corpus_kb.analysis.settings import load_analysis_settings
from corpus_kb.errors import ValidationError

PARADIGMS = ("supervised", "weak_supervision", "unsupervised", "zero_shot", "human_led")
EVIDENCE_LABELS = ("direct", "model_based", "preset_composition", "human_judgment")
INPUT_KINDS = (
    "raw_text",
    "embeddings",
    "labels",
    "seed_labels",
    "predictions",
    "timestamps",
    "ontology",
    "llm",
)
CALIBRATION_EFFORTS = ("none", "low", "moderate", "high")
AUTONOMY_LEVELS = ("automated_with_audit", "human_in_loop", "human_led")
COMPLEXITY_TIERS = ("baseline", "standard", "advanced")
SELECTION_PRIORITIES = ("first", "next", "avoid_first")
OUTPUT_FAMILIES = ("labels", "themes", "scores", "structures", "text", "errors", "recommendations")
IMPLEMENTATION_STATUSES = ("implemented_existing", "implemented_new", "human_led", "not_planned")
EXPECTED_IDS = frozenset(f"{index:02d}" for index in range(1, 31))

_VOCAB_FIELDS: dict[str, tuple[str, ...]] = {
    "paradigm": PARADIGMS,
    "evidence_label": EVIDENCE_LABELS,
    "calibration_effort": CALIBRATION_EFFORTS,
    "autonomy": AUTONOMY_LEVELS,
    "complexity_tier": COMPLEXITY_TIERS,
    "selection_priority": SELECTION_PRIORITIES,
    "output_family": OUTPUT_FAMILIES,
    "implementation_status": IMPLEMENTATION_STATUSES,
}


class MethodRecord(BaseModel):
    """One registered method with its identity, ordinal, and descriptive axes."""

    model_config = {"extra": "forbid"}

    id: str
    name: str
    summary: str
    paradigm: str
    evidence_label: str
    inputs_required: list[str]
    calibration_effort: str
    autonomy: str
    complexity_tier: str
    selection_priority: str
    selection_notes: str
    robustness_caveat: str
    bias_risk: str
    efficiency_workload: str
    output_family: str
    output_shape: str
    usage_guidance: str
    validation_required: str
    permitted_claims: str
    do_not_claim: str
    implementation_status: str
    op: str | None = None
    sources: list[str]

    @field_validator("id")
    @classmethod
    def _id_shape(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9]{2}", value):
            raise ValueError('id: use zero-padded string ids "01" to "30".')
        if not 1 <= int(value) <= 30:
            raise ValueError(f'id: {value!r} is out of range. Use ids "01" to "30".')
        return value

    @field_validator("summary")
    @classmethod
    def _summary_cap(cls, value: str) -> str:
        words = len(value.split())
        if words > 40:
            raise ValueError(f"summary: {words} words. Use 40 words or fewer.")
        return value

    @field_validator("inputs_required")
    @classmethod
    def _input_kinds(cls, values: list[str]) -> list[str]:
        unknown = [kind for kind in values if kind not in INPUT_KINDS]
        if unknown:
            raise ValueError(
                f"inputs_required: {', '.join(unknown)} is not supported. "
                f"Use one of: {', '.join(INPUT_KINDS)}."
            )
        if len(set(values)) != len(values):
            raise ValueError("inputs_required: remove duplicate input kinds.")
        return values

    @field_validator("sources")
    @classmethod
    def _sources_floor(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("sources: list at least one source URL from the document footnotes.")
        bad = [source for source in values if not source.startswith(("http://", "https://"))]
        if bad:
            raise ValueError(f"sources: {', '.join(bad)} is not a URL. Use http or https URLs.")
        return values

    @model_validator(mode="after")
    def _vocabularies_and_op(self) -> Self:
        for field_name, allowed in _VOCAB_FIELDS.items():
            value = getattr(self, field_name)
            if value not in allowed:
                raise ValueError(
                    f"{field_name}: {value!r} is not supported. Use one of: {', '.join(allowed)}."
                )
        if self.implementation_status.startswith("implemented_"):
            if not self.op:
                raise ValueError(
                    "op: statuses implemented_existing and implemented_new need an op name."
                )
        elif self.op is not None:
            raise ValueError("op: statuses human_led and not_planned must set op to null.")
        return self


class SourceDocument(BaseModel):
    """The research document the register was transcribed from."""

    model_config = {"extra": "forbid"}

    title: str
    path_note: str


class MethodRegister(BaseModel):
    """The register header plus the method rows.

    The model enforces unique ids only; ``load_method_register`` enforces
    the complete 01-30 ledger on a loaded file. Subsets stay valid so the
    rubric is testable on small synthetic registers.
    """

    model_config = {"extra": "forbid"}

    register_version: str
    source_document: SourceDocument
    selection_rules: list[str]
    adjacent_candidates: str
    methods: list[MethodRecord]

    @field_validator("register_version")
    @classmethod
    def _semver(cls, value: str) -> str:
        parts = value.split(".")
        if len(parts) != 3 or not all(part.isdigit() for part in parts):
            raise ValueError("register_version: use a semver string like 1.0.0.")
        return value

    @field_validator("selection_rules")
    @classmethod
    def _rules_present(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("selection_rules: list the selection rules in order.")
        return values

    @model_validator(mode="after")
    def _ids_unique(self) -> Self:
        ids = [record.id for record in self.methods]
        duplicated = sorted({one for one in ids if ids.count(one) > 1})
        if duplicated:
            raise ValueError(f"methods: ids must be unique. duplicated: {duplicated}.")
        return self


class MethodRecommendation(MethodRecord):
    """A ranked register row: the record verbatim plus the rank fields."""

    rank: int
    rank_reason: str
    missing_inputs: list[str]


def _packaged_register_path(file_name: str) -> Path | None:
    """The packaged mirror inside the installed ``corpus_kb`` package."""
    ref = resources.files("corpus_kb") / "config" / file_name
    if not ref.is_file():
        return None
    with resources.as_file(ref) as path:
        return path


def load_method_register(config: dict[str, object] | None = None) -> MethodRegister:
    """Load and validate the register. Repo path first, packaged mirror next."""
    settings = load_analysis_settings(config if config is not None else {})
    relative = Path(settings.method_register_path)
    repo_path = relative if relative.is_absolute() else Path.cwd() / relative
    if repo_path.exists():
        source = str(repo_path)
        text = repo_path.read_text(encoding="utf-8")
    else:
        packaged = _packaged_register_path(relative.name)
        if packaged is None:
            raise ValueError(
                f"Analysis method register not found: {settings.method_register_path}. "
                "Set analysis.method_register_path or restore the packaged mirror."
            )
        source = str(packaged)
        text = packaged.read_text(encoding="utf-8")

    raw = yaml.safe_load(text) or {}
    if not isinstance(raw, dict):
        raise ValidationError(f"Analysis method register must be a YAML mapping: {source}")
    try:
        register = MethodRegister.model_validate(raw)
    except Exception as exc:
        raise ValidationError(f"Malformed analysis method register {source}: {exc}") from exc
    ids = [record.id for record in register.methods]
    missing = sorted(EXPECTED_IDS - set(ids))
    duplicated = sorted({one for one in ids if ids.count(one) > 1})
    if missing or duplicated:
        raise ValidationError(
            f"Analysis method register {source}: expected ids 01 to 30 exactly once. "
            f"missing: {missing or 'none'}. duplicated: {duplicated or 'none'}."
        )
    return register
