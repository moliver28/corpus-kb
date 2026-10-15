"""Typed ``topics.*`` settings (Wave 4 topics layer).

Same house pattern as ``rag/contextual_settings.py`` (W3) and
``ingest/hierarchy_settings.py`` (U48): the module owns the defaults, a
frozen dataclass carries the typed reads, and a loader validates so the
YAML files, ``config.py`` defaults and this module cannot drift. The
defaults merge into every ``config.load_config()`` result through
``config._apply_typed_module_defaults`` (TOPICS_CONFIG_DEFAULTS).

Keys and their contracts:

- ``enabled``: the feature flag. ``topics_build`` refuses honestly while
  it is false (a no-op result naming the flag), never silently.
- ``method``: only ``bertopic`` exists. Any other value is rejected AT
  LOAD (config.load_config raises with the remediation text), never
  silently ignored at build time.
- ``llm_reduction``: when true, the reduction step labels (and merges)
  the raw c-TF-IDF topic labels through the configured LLM. A failed or
  missing model keeps the c-TF-IDF labels and records the note — the
  failure never raises into the run (contextualizer honesty contract).
- ``min_topic_size``: ``"auto"`` passes None to BERTopic (its own
  default) and reports the resolved value; a positive int passes
  through. Booleans are rejected (bool is an int subclass).
- ``llm`` / ``llm_base_url``: the Ollama model and host for the
  reduction step.
- ``fixture_dir`` / ``live_fallback``: the recorded-response cache.
  Fixture-first + live_fallback, SHA256-keyed, mirroring
  ``rag/contextualizer.py``.

Determinism pins are CODE CONSTANTS here (the research/inductive
convention): UMAP_RANDOM_STATE = 42, recorded in every build result —
only governance knobs live in the config.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

DEFAULT_TOPICS_ENABLED = False
DEFAULT_TOPICS_METHOD = "bertopic"
DEFAULT_LLM_REDUCTION = True
DEFAULT_MIN_TOPIC_SIZE: str | int = "auto"
DEFAULT_TOPICS_LLM = "qwen3:4b"
DEFAULT_TOPICS_LLM_BASE_URL = "http://localhost:11434"
DEFAULT_TOPICS_FIXTURE_DIR = "tests/fixtures/topics_recorded"
DEFAULT_TOPICS_LIVE_FALLBACK = False

SUPPORTED_TOPIC_METHODS = frozenset({"bertopic"})

# Determinism pin (the research/inductive r7 convention): the UMAP random
# state is a code constant, recorded in every build result, never a knob.
UMAP_RANDOM_STATE = 42

# BERTopic's own min_topic_size default; "auto" resolves to this and the
# resolved value is reported (the honest-defaults convention).
AUTO_MIN_TOPIC_SIZE = 10

TOPICS_CONFIG_DEFAULTS: dict[str, object] = {
    "enabled": DEFAULT_TOPICS_ENABLED,
    "method": DEFAULT_TOPICS_METHOD,
    "llm_reduction": DEFAULT_LLM_REDUCTION,
    "min_topic_size": DEFAULT_MIN_TOPIC_SIZE,
    "llm": DEFAULT_TOPICS_LLM,
    "llm_base_url": DEFAULT_TOPICS_LLM_BASE_URL,
    "fixture_dir": DEFAULT_TOPICS_FIXTURE_DIR,
    "live_fallback": DEFAULT_TOPICS_LIVE_FALLBACK,
}


@dataclass(frozen=True)
class TopicsSettings:
    """Typed ``topics.*`` reads (Wave 4)."""

    enabled: bool = DEFAULT_TOPICS_ENABLED
    method: str = DEFAULT_TOPICS_METHOD
    llm_reduction: bool = DEFAULT_LLM_REDUCTION
    min_topic_size: str | int = DEFAULT_MIN_TOPIC_SIZE
    llm: str = DEFAULT_TOPICS_LLM
    llm_base_url: str = DEFAULT_TOPICS_LLM_BASE_URL
    fixture_dir: str | None = DEFAULT_TOPICS_FIXTURE_DIR
    live_fallback: bool = DEFAULT_TOPICS_LIVE_FALLBACK

    def resolved_min_topic_size(self) -> int:
        """The int BERTopic actually receives ("auto" resolves here)."""
        if isinstance(self.min_topic_size, str):
            return AUTO_MIN_TOPIC_SIZE
        return int(self.min_topic_size)


def _min_topic_size_error(value: object) -> str | None:
    """``"auto"`` or a positive int (bools are ints — reject them)."""
    if isinstance(value, bool):
        return (
            "topics.min_topic_size: must be the string 'auto' or a positive "
            f"int (booleans are not sizes); got {value!r}"
        )
    if isinstance(value, str):
        if value == "auto":
            return None
        return (
            "topics.min_topic_size: must be the string 'auto' or a positive "
            f"int; got {value!r}"
        )
    if isinstance(value, int) and value > 0:
        return None
    return (
        "topics.min_topic_size: must be the string 'auto' or a positive int "
        f"(the smallest cluster BERTopic may form); got {value!r}"
    )


def topics_settings_error(config: dict[str, object]) -> str | None:
    """Remediation text for invalid topics values, or None when valid.

    Only VALUES are rejected — a partial block (missing keys) is fine and
    fills from the defaults above. Called from ``config.load_config`` so
    an unknown method fails AT LOAD, never mid-build.
    """
    block = _topics_block(config)
    method = block.get("method", DEFAULT_TOPICS_METHOD)
    if method not in SUPPORTED_TOPIC_METHODS:
        methods = ", ".join(sorted(SUPPORTED_TOPIC_METHODS))
        return (
            f"topics.method: {method!r} is not supported. Only {methods} is "
            "available. Set method: bertopic or remove the key."
        )
    size_error = _min_topic_size_error(block.get("min_topic_size", DEFAULT_MIN_TOPIC_SIZE))
    if size_error is not None:
        return size_error
    for key in ("llm", "llm_base_url"):
        value = block.get(key, DEFAULT_TOPICS_LLM if key == "llm" else DEFAULT_TOPICS_LLM_BASE_URL)
        if not isinstance(value, str) or not value:
            return f"topics.{key}: must be a non-empty string; got {value!r}"
    return None


def load_topics_settings(config: dict[str, object]) -> TopicsSettings:
    """Read ``topics.*`` with defaults; invalid values raise.

    Raises ``ValueError`` with the same remediation text load_config uses,
    so direct readers (tests, tools) fail as loudly as a config load.
    """
    error = topics_settings_error(config)
    if error is not None:
        raise ValueError(error)
    block = _topics_block(config)
    fixture = block.get("fixture_dir", DEFAULT_TOPICS_FIXTURE_DIR)
    size = block.get("min_topic_size", DEFAULT_MIN_TOPIC_SIZE)
    typed_size: str | int
    if isinstance(size, bool):
        # Unreachable behind topics_settings_error, but keep the type honest.
        raise ValueError(_min_topic_size_error(size) or "invalid topics.min_topic_size")
    typed_size = size if isinstance(size, int) else str(size)
    return TopicsSettings(
        enabled=bool(block.get("enabled", DEFAULT_TOPICS_ENABLED)),
        method=str(block.get("method", DEFAULT_TOPICS_METHOD)),
        llm_reduction=bool(block.get("llm_reduction", DEFAULT_LLM_REDUCTION)),
        min_topic_size=typed_size,
        llm=str(block.get("llm", DEFAULT_TOPICS_LLM)),
        llm_base_url=str(block.get("llm_base_url", DEFAULT_TOPICS_LLM_BASE_URL)),
        fixture_dir=str(fixture) if fixture else None,
        live_fallback=bool(block.get("live_fallback", DEFAULT_TOPICS_LIVE_FALLBACK)),
    )


def _topics_block(config: dict[str, object]) -> dict[str, object]:
    """The ``topics`` mapping, or an empty one."""
    block = config.get("topics")
    if not isinstance(block, dict):
        return {}
    return cast(dict[str, object], block)
