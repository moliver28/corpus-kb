"""Pluggable ingestion strategies (plan Section 0): the ONE pipeline seam.

Public surface: the strategy contract (:class:`IngestionStrategy`,
:class:`ChunkPlan`), the registry (:func:`register_strategy`,
:func:`resolve_strategy`), and conformance (:func:`validate_plan`).
``run_pipeline`` resolves one strategy per document; embed/extract/store
never see strategies.
"""

from __future__ import annotations

from .base import (
    DEFAULT_STRATEGY_NAME,
    DETECT_ADOPT_CONFIDENCE,
    DETECT_HEAD_CHARS,
    QA_CONTEXT_KEY,
    TOKEN_BUDGET_CHARS,
    TRANSCRIPT_STRATEGY_NAME,
    ChunkPlan,
    DetectResult,
    IngestionStrategy,
    PlannedChunk,
    SourceProfile,
    chunks_from_plan,
    planned_from_chunk,
    validate_plan,
)
from .registry import (
    ENTRY_POINT_GROUP,
    StrategyResolution,
    UnknownStrategyError,
    get_strategy,
    register_strategy,
    resolve_strategy,
    strategy_names,
)

__all__ = [
    "DEFAULT_STRATEGY_NAME",
    "DETECT_ADOPT_CONFIDENCE",
    "DETECT_HEAD_CHARS",
    "ENTRY_POINT_GROUP",
    "QA_CONTEXT_KEY",
    "TOKEN_BUDGET_CHARS",
    "TRANSCRIPT_STRATEGY_NAME",
    "ChunkPlan",
    "DetectResult",
    "IngestionStrategy",
    "PlannedChunk",
    "SourceProfile",
    "StrategyResolution",
    "UnknownStrategyError",
    "chunks_from_plan",
    "get_strategy",
    "planned_from_chunk",
    "register_strategy",
    "resolve_strategy",
    "strategy_names",
    "validate_plan",
]
