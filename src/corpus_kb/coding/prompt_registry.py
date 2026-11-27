"""Prompt registry (TD-03, R2): one YAML 1.2 file per prompt, strict variables.

Header keys: ``id``, ``version``, ``kind`` (``cell`` or ``group_boundary``),
``changelog``, ``variables``, ``defaults``, ``body`` (AR-5). The body holds
``{{name}}`` placeholders and nothing that executes (N8). The renderer
checks that the supplied variables equal the declared set, fills declared
defaults, substitutes, and answers a SHA-256 digest over the id, the
version, and the variable-value digests. Callers record the triple in the
step ledger (TD-13). This is the shared loader for the W6C prompts too.
The typed errors are domain-local taxonomy subclasses (``CodebookError``
precedent): ``PromptRenderError`` maps to ``validation_error`` and
``PromptNotFoundError`` to ``not_found``; ``errors.py`` stays untouched.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import cache
from typing import Any

import yaml

from corpus_kb.errors import NotFoundError, ValidationError

__all__ = [
    "PromptNotFoundError",
    "PromptRenderError",
    "PromptSpec",
    "PromptVariable",
    "RenderedPrompt",
    "load_prompt",
    "render_prompt",
]

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
_ID_PATTERN = re.compile(r"[a-z][a-z0-9_]*")
_HEADER_KEYS = {"id", "version", "kind", "changelog", "variables", "defaults", "body"}
_KINDS = {"cell", "group_boundary"}
_DEFAULT_KEYS = {"temperature", "seed", "max_tokens", "json_schema"}
_VARIABLE_KEYS = {"name", "description", "required", "default"}


class PromptNotFoundError(NotFoundError):
    """No registry prompt carries this id."""


class PromptRenderError(ValidationError):
    """The prompt render failed: bad file, missing required variable, or an
    unknown variable. The message names the prompt and the problem."""


@dataclass(frozen=True)
class PromptVariable:
    """One declared body variable (TD-03 point 1)."""

    name: str
    description: str
    required: bool
    default: str


@dataclass(frozen=True)
class PromptSpec:
    """One loaded prompt file: header plus body."""

    id: str
    version: str
    kind: str
    changelog: str
    variables: tuple[PromptVariable, ...]
    defaults: dict[str, Any]
    body: str


@dataclass(frozen=True)
class RenderedPrompt:
    """One render: the text plus the provenance triple the ledger records."""

    prompt_id: str
    prompt_version: str
    prompt_kind: str
    prompt_digest: str
    rendered: str
    defaults: dict[str, Any]


def _digest(text: str) -> str:
    """SHA-256 hex digest over UTF-8 bytes (FIPS 180-4, AR-6)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _variable(raw: object, prompt_id: str) -> PromptVariable:
    """One declared variable row, validated (unknown keys fail loud)."""
    if not isinstance(raw, dict):
        raise PromptRenderError(f"prompt {prompt_id}: each variable must be an object")
    unknown = set(raw) - _VARIABLE_KEYS
    if unknown:
        raise PromptRenderError(f"prompt {prompt_id}: variable has unknown keys {sorted(unknown)}")
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise PromptRenderError(f"prompt {prompt_id}: a variable has no name")
    default = raw.get("default", "")
    return PromptVariable(
        name=name,
        description=str(raw.get("description", "")),
        required=bool(raw.get("required", False)),
        default="" if default is None else str(default),
    )


def _spec_from_doc(prompt_id: str, doc: object) -> PromptSpec:
    """Validate one parsed YAML document into a PromptSpec (pure)."""
    if not isinstance(doc, dict):
        raise PromptRenderError(f"prompt {prompt_id}: file must hold one YAML object")
    unknown = set(doc) - _HEADER_KEYS
    if unknown:
        raise PromptRenderError(f"prompt {prompt_id}: unknown header keys {sorted(unknown)}")
    for key in ("id", "version", "body"):
        if not isinstance(doc.get(key), str) or not doc.get(key):
            raise PromptRenderError(f"prompt {prompt_id}: header key {key!r} must be text")
    if doc["id"] != prompt_id:
        raise PromptRenderError(f"prompt {prompt_id}: header id {doc['id']!r} does not match")
    kind = doc.get("kind", "cell")
    if kind not in _KINDS:
        raise PromptRenderError(f"prompt {prompt_id}: kind must be one of {sorted(_KINDS)}")
    raw_vars = doc.get("variables", [])
    if not isinstance(raw_vars, list):
        raise PromptRenderError(f"prompt {prompt_id}: variables must be a list")
    variables = tuple(_variable(raw, prompt_id) for raw in raw_vars)
    defaults = doc.get("defaults", {})
    if not isinstance(defaults, dict) or set(defaults) - _DEFAULT_KEYS:
        bad = sorted(set(defaults) - _DEFAULT_KEYS) if isinstance(defaults, dict) else None
        raise PromptRenderError(f"prompt {prompt_id}: defaults has unknown keys {bad}")
    declared = {v.name for v in variables}
    body = str(doc["body"])
    placeholders = set(_PLACEHOLDER.findall(body))
    if placeholders - declared:
        raise PromptRenderError(
            f"prompt {prompt_id}: body uses undeclared variables {sorted(placeholders - declared)}"
        )
    missing_from_body = sorted(declared - placeholders)
    if missing_from_body:
        raise PromptRenderError(
            f"prompt {prompt_id}: declared variables missing from body {missing_from_body}"
        )
    return PromptSpec(
        id=prompt_id,
        version=str(doc["version"]),
        kind=str(kind),
        changelog=str(doc.get("changelog", "")),
        variables=variables,
        defaults=dict(defaults),
        body=body,
    )


@cache
def load_prompt(prompt_id: str) -> PromptSpec:
    """Load and validate one prompt file from the packaged ``prompts/`` dir."""
    if not _ID_PATTERN.fullmatch(prompt_id):
        raise PromptNotFoundError(f"no prompt named {prompt_id!r}")
    from importlib import resources

    path = resources.files("corpus_kb.coding").joinpath("prompts", f"{prompt_id}.yaml")
    if not path.is_file():
        raise PromptNotFoundError(f"no prompt named {prompt_id!r}")
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PromptRenderError(f"prompt {prompt_id}: unparsable YAML: {exc}") from exc
    return _spec_from_doc(prompt_id, doc)


def render_prompt(prompt_id: str, variables: dict[str, Any] | None) -> RenderedPrompt:
    """Render one prompt: exact variable-set check, defaults, digest.

    The supplied keys must equal the declared set: an unknown key and a
    missing required key are typed errors (TD-03 point 3). Declared
    optional variables fill from their defaults.
    """
    spec = load_prompt(prompt_id)
    supplied = variables if isinstance(variables, dict) else {}
    declared = {v.name for v in spec.variables}
    unknown = sorted(set(supplied) - declared)
    if unknown:
        raise PromptRenderError(f"prompt {prompt_id}: unknown variables {unknown}")
    required = {v.name for v in spec.variables if v.required}
    missing = sorted(required - set(supplied))
    if missing:
        raise PromptRenderError(f"prompt {prompt_id}: missing required variables {missing}")
    values = {v.name: str(supplied.get(v.name, v.default)) for v in spec.variables}

    def _sub(match: re.Match[str]) -> str:
        return values[match.group(1)]

    rendered = _PLACEHOLDER.sub(_sub, spec.body)
    parts = [spec.id, spec.version]
    parts.extend(f"{name}:{_digest(values[name])}" for name in sorted(values))
    return RenderedPrompt(
        prompt_id=spec.id,
        prompt_version=spec.version,
        prompt_kind=spec.kind,
        prompt_digest=_digest("\n".join(parts)),
        rendered=rendered,
        defaults=dict(spec.defaults),
    )
