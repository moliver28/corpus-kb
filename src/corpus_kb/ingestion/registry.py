"""Ingestion strategy registry: registration, plugin discovery, resolution.

Resolution precedence (plan Section 0, pinned):
  1. EXPLICIT override (CLI --source-type / MCP tool param — a strategy name)
  2. project config mapping ``ingestion.strategies[source_type]``
  3. content detection (each registered strategy's detect() on the head)
  4. ``ingestion.default`` (a strategy name, default "default")

Built-ins register directly in code (see :func:`_ensure_ready`); third-party
strategies load ONCE through the importlib.metadata entry-point group
``corpus_kb.ingestion_strategies``. A broken plugin logs a warning and is
skipped — it never crashes ingest.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .base import (
    DEFAULT_STRATEGY_NAME,
    DETECT_ADOPT_CONFIDENCE,
    DetectResult,
    IngestionStrategy,
)

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "corpus_kb.ingestion_strategies"


class UnknownStrategyError(ValueError):
    """A strategy name (explicit override or config mapping) is not registered."""


_REGISTRY: dict[str, IngestionStrategy] = {}
_ready = False


def register_strategy(strategy: IngestionStrategy) -> None:
    """Register a strategy instance under its ``name`` (idempotent overwrite).

    In-process registration is extension ladder #2 (scripts/power users);
    entry points are ladder #3; the config mapping is ladder #1.
    """
    name = getattr(strategy, "name", None)
    if not isinstance(name, str) or not name:
        raise ValueError("Ingestion strategy must expose a non-empty string 'name'")
    if not callable(getattr(strategy, "detect", None)) or not callable(
        getattr(strategy, "build_chunks", None)
    ):
        raise ValueError(f"Ingestion strategy {name!r} must implement detect() and build_chunks()")
    _REGISTRY[name] = strategy


def strategy_names() -> list[str]:
    """All registered strategy names (built-ins + discovered plugins)."""
    _ensure_ready()
    return list(_REGISTRY)


def get_strategy(name: str) -> IngestionStrategy:
    """Look up a registered strategy; unknown names raise with the valid set."""
    _ensure_ready()
    strategy = _REGISTRY.get(name)
    if strategy is None:
        raise UnknownStrategyError(
            f"Unknown ingestion strategy '{name}'. Valid strategies: {', '.join(sorted(_REGISTRY))}"
        )
    return strategy


def _ensure_ready() -> None:
    """Register built-ins and discover entry-point plugins (once per process)."""
    global _ready
    if _ready:
        return
    _ready = True
    from .strategies.code import CodeStrategy
    from .strategies.default import DefaultStrategy
    from .strategies.markdown_sections import MarkdownSectionsStrategy
    from .strategies.pptx import PptxStructureStrategy
    from .strategies.survey import SurveyStrategy
    from .strategies.transcript import TranscriptStrategy
    from .strategies.web import WebStructureStrategy

    # Registration order is detection tie-breaking order (first wins a tie
    # in _detect_best): specific claimants before the generic fallbacks.
    register_strategy(DefaultStrategy())
    register_strategy(PptxStructureStrategy())
    register_strategy(TranscriptStrategy())
    register_strategy(WebStructureStrategy())
    register_strategy(CodeStrategy())
    register_strategy(MarkdownSectionsStrategy())
    register_strategy(SurveyStrategy())
    _discover_entry_points()


def _discover_entry_points() -> list[str]:
    """Load third-party strategies from the entry-point group (error-tolerant).

    Returns the names successfully registered. Import/loading failures are
    logged as warnings and skipped: a broken third-party plugin must never
    take ingest down.
    """
    loaded: list[str] = []
    try:
        from importlib.metadata import entry_points

        plugins = entry_points(group=ENTRY_POINT_GROUP)
    except Exception as exc:  # metadata scan itself failed
        logger.warning("Ingestion strategy entry-point scan failed: %s", exc)
        return loaded
    for plugin in plugins:
        try:
            factory = plugin.load()
            strategy = factory() if isinstance(factory, type) else factory
            register_strategy(strategy)
            loaded.append(str(getattr(strategy, "name", plugin.name)))
        except Exception as exc:
            logger.warning("Skipping broken ingestion-strategy plugin %r: %s", plugin.name, exc)
    return loaded


@dataclass(frozen=True)
class StrategyResolution:
    """The ONE strategy chosen for a document, with full provenance.

    ``detected`` names the strategy that won content detection regardless of
    which basis actually decided (detection always runs once, for provenance).
    """

    strategy: IngestionStrategy
    basis: str  # "explicit" | "profile" | "config" | "detection" | "default"
    detection: DetectResult | None = None
    detected: str | None = None
    mapped_source_type: str | None = None


def resolve_strategy(
    source_type: str,
    filename: str,
    raw_head: str,
    config: dict[str, object],
    strategy_override: str | None = None,
    profile: object | None = None,
) -> StrategyResolution:
    """Resolve ONE strategy for one document (precedence pinned in Section 0).

    Precedence: explicit override > profile mapping > config mapping >
    content detection > default (the profile's default wins over the
    config's when a profile is active). ``profile`` is a
    :class:`corpus_kb.profiles.IngestionProfile` (typed loosely so the
    registry never imports the profiles package).

    Raises :class:`UnknownStrategyError` for an unknown explicit override or
    an unknown config-mapped strategy name (loud, never a silent fallback —
    the house pattern from extract_with_fallback).
    """
    _ensure_ready()
    detected_strategy, detection = _detect_best(source_type, filename, raw_head)
    detected_name = detected_strategy.name if detected_strategy is not None else None
    if strategy_override is not None:
        return StrategyResolution(
            get_strategy(strategy_override), "explicit", detection, detected_name
        )
    mapped_name = _profile_strategy_name(source_type, profile)
    if mapped_name is not None:
        return StrategyResolution(
            get_strategy(mapped_name), "profile", detection, detected_name, source_type
        )
    mapped_name = _config_strategy_name(source_type, config)
    if mapped_name is not None:
        return StrategyResolution(
            get_strategy(mapped_name), "config", detection, detected_name, source_type
        )
    if (
        detected_strategy is not None
        and detection is not None
        and detection.confidence >= DETECT_ADOPT_CONFIDENCE
    ):
        return StrategyResolution(detected_strategy, "detection", detection, detected_name)
    return StrategyResolution(
        get_strategy(_default_strategy_name(config, profile)), "default", detection, detected_name
    )


def _profile_strategy_name(source_type: str, profile: object | None) -> str | None:
    """The strategy name the active profile maps for this source type."""
    if profile is None:
        return None
    raw = getattr(profile, "strategies", None)
    if not isinstance(raw, dict):
        return None
    value = raw.get(source_type)
    return value if isinstance(value, str) and value else None


def _detect_best(
    source_type: str, filename: str, raw_head: str
) -> tuple[IngestionStrategy | None, DetectResult | None]:
    """Run every strategy's detect(); return the highest-confidence claim.

    A detect() that raises is logged and skipped — detection is best-effort
    provenance, never a failure path.
    """
    best: tuple[float, IngestionStrategy, DetectResult] | None = None
    for name, strategy in _REGISTRY.items():
        try:
            result = strategy.detect(source_type, filename, raw_head)
        except Exception as exc:
            logger.warning("Strategy %s detect() failed: %s", name, exc)
            continue
        if best is None or result.confidence > best[0]:
            best = (result.confidence, strategy, result)
    if best is None:
        return None, None
    return best[1], best[2]


def _ingestion_config(config: dict[str, object]) -> dict[str, object]:
    value = config.get("ingestion")
    return value if isinstance(value, dict) else {}


def _config_strategy_name(source_type: str, config: dict[str, object]) -> str | None:
    """The strategy name mapped for this document source_type, if any."""
    strategies = _ingestion_config(config).get("strategies")
    if not isinstance(strategies, dict):
        return None
    raw = strategies.get(source_type)
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw:
        raise UnknownStrategyError(
            f"ingestion.strategies[{source_type!r}] must name a strategy, got {raw!r}"
        )
    return raw


def _default_strategy_name(config: dict[str, object], profile: object | None = None) -> str:
    if profile is not None:
        raw = getattr(profile, "default_strategy", None)
        if isinstance(raw, str) and raw:
            return raw
    raw = _ingestion_config(config).get("default")
    return raw if isinstance(raw, str) and raw else DEFAULT_STRATEGY_NAME
