"""Typed ``ingest.contextualize.*`` settings (W3 contextualize v2).

Same house pattern as ``rag/rerank_settings.py`` (U30) and
``ingest/hierarchy_settings.py`` (U48): the module owns the defaults, a
frozen dataclass carries the typed reads, and a loader validates so the
YAML files, ``config.py`` defaults and this module cannot drift. W1 moved
the block from ``contextual.*``; the loader's alias layer already copies
legacy values in, so this reader only ever sees canonical keys.

W3 keys and their contracts:

- ``backend``: only ``ollama`` is available. Any other value is rejected
  AT LOAD (config.load_config raises with the remediation text), never
  silently ignored at ingest time.
- ``model``: default ``qwen3:8b`` (the program target). Honest fallback
  unchanged: a missing model or a down Ollama degrades to an empty blurb;
  nothing is pulled automatically.
- ``prompt_profile``: ``interview`` (research/interview template, EXPLICIT
  for transcript sources — mentions the moderator question) or ``generic``
  (neutral, any document). Unknown profiles are rejected at load.
- ``max_context_tokens``: cap on the GENERATED blurb (chars/4 estimate,
  the chunking/hierarchy.py convention). The blurb truncates at the last
  sentence or word boundary within the cap.
- ``document_first_prompt``: true = document context first, then the chunk
  (the Anthropic-style recommendation); false = chunk-first variant.
  Profiles x orderings = 4 template combinations.
- ``apply_to``: WRITE-TIME targets of the blurb, a non-empty subset of
  ``[embedding, lexical]``. ``embedding`` puts ``blurb\\n\\ntext`` into the
  embed input; ``lexical`` writes the ``chunks.context_blurb`` column the
  FTS expression reads. The default ``[embedding, lexical]`` reproduces
  the pre-W3 behavior exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

DEFAULT_CONTEXTUAL_ENABLED = False
DEFAULT_ENABLED_SOURCE_TYPES: list[str] = ["interview", "research"]
DEFAULT_CONTEXTUAL_MODEL = "qwen3:8b"
DEFAULT_CONTEXTUAL_BASE_URL = "http://localhost:11434"
DEFAULT_CONTEXTUAL_TEMPERATURE = 0.0
DEFAULT_CONTEXTUAL_FIXTURE_DIR = "tests/fixtures/contextual_recorded"
DEFAULT_LIVE_FALLBACK = False
DEFAULT_CONTEXTUAL_BACKEND = "ollama"
DEFAULT_PROMPT_PROFILE = "interview"
DEFAULT_MAX_CONTEXT_TOKENS = 100
DEFAULT_DOCUMENT_FIRST_PROMPT = True
DEFAULT_APPLY_TO: list[str] = ["embedding", "lexical"]

SUPPORTED_CONTEXTUAL_BACKENDS = frozenset({"ollama"})
SUPPORTED_PROMPT_PROFILES = frozenset({"interview", "generic"})
SUPPORTED_APPLY_TO_TARGETS = frozenset({"embedding", "lexical"})

CONTEXTUAL_CONFIG_DEFAULTS: dict[str, Any] = {
    "enabled": DEFAULT_CONTEXTUAL_ENABLED,
    "enabled_source_types": list(DEFAULT_ENABLED_SOURCE_TYPES),
    "model": DEFAULT_CONTEXTUAL_MODEL,
    "base_url": DEFAULT_CONTEXTUAL_BASE_URL,
    "temperature": DEFAULT_CONTEXTUAL_TEMPERATURE,
    "fixture_dir": DEFAULT_CONTEXTUAL_FIXTURE_DIR,
    "live_fallback": DEFAULT_LIVE_FALLBACK,
    "backend": DEFAULT_CONTEXTUAL_BACKEND,
    "prompt_profile": DEFAULT_PROMPT_PROFILE,
    "max_context_tokens": DEFAULT_MAX_CONTEXT_TOKENS,
    "document_first_prompt": DEFAULT_DOCUMENT_FIRST_PROMPT,
    "apply_to": list(DEFAULT_APPLY_TO),
}


@dataclass(frozen=True)
class ContextualSettings:
    """Typed ``ingest.contextualize.*`` reads (W3)."""

    enabled: bool = DEFAULT_CONTEXTUAL_ENABLED
    enabled_source_types: list[str] = field(
        default_factory=lambda: list(DEFAULT_ENABLED_SOURCE_TYPES)
    )
    model: str = DEFAULT_CONTEXTUAL_MODEL
    base_url: str = DEFAULT_CONTEXTUAL_BASE_URL
    temperature: float = DEFAULT_CONTEXTUAL_TEMPERATURE
    fixture_dir: str | None = DEFAULT_CONTEXTUAL_FIXTURE_DIR
    live_fallback: bool = DEFAULT_LIVE_FALLBACK
    backend: str = DEFAULT_CONTEXTUAL_BACKEND
    prompt_profile: str = DEFAULT_PROMPT_PROFILE
    max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS
    document_first_prompt: bool = DEFAULT_DOCUMENT_FIRST_PROMPT
    apply_to: list[str] = field(default_factory=lambda: list(DEFAULT_APPLY_TO))


def _apply_to_error(value: object) -> str | None:
    """Non-empty subset-of-{embedding, lexical} rule for ``apply_to``."""
    if not isinstance(value, list) or not value:
        return (
            "ingest.contextualize.apply_to: must be a non-empty list drawn from "
            "['embedding', 'lexical'] (which write-time targets the blurb reaches); "
            f"got {value!r}"
        )
    bad = [
        item for item in value if not (isinstance(item, str) and item in SUPPORTED_APPLY_TO_TARGETS)
    ]
    if bad:
        return (
            "ingest.contextualize.apply_to: unknown target(s) "
            f"{', '.join(repr(b) for b in bad)}; supported targets are "
            "'embedding' (blurb into the embed input) and 'lexical' (blurb "
            "into the chunks.context_blurb FTS column)"
        )
    return None


def contextualize_settings_error(config: dict[str, Any]) -> str | None:
    """Remediation text for invalid contextualize values, or None when valid.

    Only VALUES are rejected — a partial block (missing keys) is fine and
    fills from the defaults above. Called from ``config.load_config`` so a
    bad backend/profile/apply_to fails AT LOAD, never mid-ingest.
    """
    block = _contextualize_block(config)
    backend = block.get("backend", DEFAULT_CONTEXTUAL_BACKEND)
    if backend not in SUPPORTED_CONTEXTUAL_BACKENDS:
        backends = ", ".join(sorted(SUPPORTED_CONTEXTUAL_BACKENDS))
        return (
            f"ingest.contextualize.backend: {backend!r} is not supported. Only "
            f"{backends} is available. Set backend: ollama or remove the key."
        )
    profile = block.get("prompt_profile", DEFAULT_PROMPT_PROFILE)
    if profile not in SUPPORTED_PROMPT_PROFILES:
        profiles = ", ".join(sorted(SUPPORTED_PROMPT_PROFILES))
        return (
            f"ingest.contextualize.prompt_profile: unknown profile {profile!r}. "
            f"Supported profiles: {profiles}."
        )
    apply_error = _apply_to_error(block.get("apply_to", DEFAULT_APPLY_TO))
    if apply_error is not None:
        return apply_error
    tokens = block.get("max_context_tokens", DEFAULT_MAX_CONTEXT_TOKENS)
    if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens <= 0:
        return (
            "ingest.contextualize.max_context_tokens: must be a positive int "
            f"(the generated-blurb cap, chars/4 estimated); got {tokens!r}"
        )
    return None


def load_contextual_settings(config: dict[str, Any]) -> ContextualSettings:
    """Read ``ingest.contextualize.*`` with defaults; invalid values raise.

    Raises ``ValueError`` with the same remediation text load_config uses,
    so direct readers (tests, tools) fail as loudly as a config load.
    """
    error = contextualize_settings_error(config)
    if error is not None:
        raise ValueError(error)
    block = _contextualize_block(config)
    fixture = block.get("fixture_dir", DEFAULT_CONTEXTUAL_FIXTURE_DIR)
    enabled_source_types = block.get("enabled_source_types", DEFAULT_ENABLED_SOURCE_TYPES)
    apply_to = block.get("apply_to", DEFAULT_APPLY_TO)
    return ContextualSettings(
        enabled=bool(block.get("enabled", DEFAULT_CONTEXTUAL_ENABLED)),
        enabled_source_types=[str(s) for s in cast(list[object], enabled_source_types)],
        model=str(block.get("model", DEFAULT_CONTEXTUAL_MODEL)),
        base_url=str(block.get("base_url", DEFAULT_CONTEXTUAL_BASE_URL)),
        temperature=float(block.get("temperature", DEFAULT_CONTEXTUAL_TEMPERATURE)),
        fixture_dir=str(fixture) if fixture else None,
        live_fallback=bool(block.get("live_fallback", DEFAULT_LIVE_FALLBACK)),
        backend=str(block.get("backend", DEFAULT_CONTEXTUAL_BACKEND)),
        prompt_profile=str(block.get("prompt_profile", DEFAULT_PROMPT_PROFILE)),
        max_context_tokens=int(block.get("max_context_tokens", DEFAULT_MAX_CONTEXT_TOKENS)),
        document_first_prompt=bool(
            block.get("document_first_prompt", DEFAULT_DOCUMENT_FIRST_PROMPT)
        ),
        apply_to=[str(t) for t in cast(list[object], apply_to)],
    )


def _contextualize_block(config: dict[str, Any]) -> dict[str, Any]:
    """The ``ingest.contextualize`` mapping, or an empty one."""
    ingest = config.get("ingest")
    if not isinstance(ingest, dict):
        return {}
    block = ingest.get("contextualize")
    if not isinstance(block, dict):
        return {}
    return cast(dict[str, Any], block)
