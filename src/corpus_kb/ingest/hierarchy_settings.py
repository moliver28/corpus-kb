"""U48/P6: typed settings for the multi-granularity token hierarchy.

The house pattern is ``research/search_settings.py`` (U20) and
``research/retrieval_settings.py`` (U45/U21): a frozen dataclass, module-level
defaults, and a ``load_*_settings(config)`` reader so the YAML files,
``config.py`` defaults and this module cannot drift.

Keys (all under ``ingest.chunking`` — renamed from ``ingest.hierarchy`` in
the config-v2 wave; the legacy namespace is an alias the loader applies):

* ``enabled`` — the SHADOW-path flag (default True): ingest writes the
  child/chunk/parent hierarchy into the shadow tables alongside the
  untouched v1 ``chunks``/``chunks_vectors`` rows. v1 stays the live search
  corpus until the separately-approved cutover.
* ``child_tokens`` / ``chunk_tokens`` / ``parent_tokens`` — the three
  hierarchy levels (committed upgrade-program defaults 128/512/2048).
* ``overlap_tokens`` — token overlap carried between adjacent windows at
  every level (committed default 64).
* ``respect_boundaries`` — natural boundary kinds the packer snaps window
  edges to where the path provides them: ``speaker_turn``/``qa_pair``
  (research-grain path), ``slide``/``heading`` (main RAG path).

The query-time ``expand_to_parent`` arm moved to ``retrieval.*`` (it is a
retrieval behavior, not an ingest shape); it is read by
``research/retrieval_settings.py`` and consumed by ``QueryHandler``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_CHILD_TOKENS = 128
DEFAULT_CHUNK_TOKENS = 512
DEFAULT_PARENT_TOKENS = 2048
DEFAULT_OVERLAP_TOKENS = 64
DEFAULT_RESPECT_BOUNDARIES: tuple[str, ...] = (
    "speaker_turn",
    "qa_pair",
    "slide",
    "heading",
)

CHUNKING_CONFIG_DEFAULTS: dict[str, object] = {
    "enabled": True,
    "child_tokens": DEFAULT_CHILD_TOKENS,
    "chunk_tokens": DEFAULT_CHUNK_TOKENS,
    "parent_tokens": DEFAULT_PARENT_TOKENS,
    "overlap_tokens": DEFAULT_OVERLAP_TOKENS,
    "respect_boundaries": list(DEFAULT_RESPECT_BOUNDARIES),
}

# Backwards-compatible alias for the pre-v2 name (imports and tests read it).
HIERARCHY_CONFIG_DEFAULTS = CHUNKING_CONFIG_DEFAULTS

_KNOWN_BOUNDARY_KINDS = frozenset({"speaker_turn", "qa_pair", "slide", "heading", "symbol"})


@dataclass(frozen=True)
class HierarchySettings:
    """Typed reads for the ``ingest.chunking.*`` knobs (U48/P6)."""

    enabled: bool = True
    child_tokens: int = DEFAULT_CHILD_TOKENS
    chunk_tokens: int = DEFAULT_CHUNK_TOKENS
    parent_tokens: int = DEFAULT_PARENT_TOKENS
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS
    respect_boundaries: tuple[str, ...] = DEFAULT_RESPECT_BOUNDARIES
    extra: dict[str, object] = field(default_factory=dict)


def _positive_int(value: object, default: int, name: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"ingest.chunking.{name} must be an int; got {value!r}")
    if value <= 0:
        raise ValueError(f"ingest.chunking.{name} must be a positive int; got {value}")
    return value


def _flag(value: object, default: bool, name: str) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"ingest.chunking.{name} must be a bool; got {value!r}")
    return value


def _boundaries(value: object) -> tuple[str, ...]:
    if value is None:
        return DEFAULT_RESPECT_BOUNDARIES
    if not isinstance(value, (list, tuple)):
        raise ValueError(
            f"ingest.chunking.respect_boundaries must be a list of strings; got {value!r}"
        )
    items = tuple(value)
    if not all(isinstance(item, str) for item in items):
        raise ValueError(
            f"ingest.chunking.respect_boundaries must be a list of strings; got {value!r}"
        )
    unknown = [item for item in items if item not in _KNOWN_BOUNDARY_KINDS]
    if unknown:
        raise ValueError(
            f"ingest.chunking.respect_boundaries has unknown kinds {unknown}; "
            f"known kinds: {sorted(_KNOWN_BOUNDARY_KINDS)}"
        )
    return items


def load_hierarchy_settings(config: dict[str, object]) -> HierarchySettings:
    """Read the ``ingest.chunking.*`` knobs, defaulting to the module constants.

    Invalid values raise ``ValueError`` — a misconfigured token budget must
    fail loudly rather than silently change the shadow hierarchy shape.
    """
    ingest = config.get("ingest", {})
    ingest_block: dict[str, object] = ingest if isinstance(ingest, dict) else {}
    raw = ingest_block.get("chunking", {})
    block: dict[str, object] = raw if isinstance(raw, dict) else {}
    return HierarchySettings(
        enabled=_flag(block.get("enabled"), True, "enabled"),
        child_tokens=_positive_int(block.get("child_tokens"), DEFAULT_CHILD_TOKENS, "child_tokens"),
        chunk_tokens=_positive_int(block.get("chunk_tokens"), DEFAULT_CHUNK_TOKENS, "chunk_tokens"),
        parent_tokens=_positive_int(
            block.get("parent_tokens"), DEFAULT_PARENT_TOKENS, "parent_tokens"
        ),
        overlap_tokens=_positive_int(
            block.get("overlap_tokens"), DEFAULT_OVERLAP_TOKENS, "overlap_tokens"
        ),
        respect_boundaries=_boundaries(block.get("respect_boundaries")),
        extra={key: value for key, value in block.items() if key not in CHUNKING_CONFIG_DEFAULTS},
    )
