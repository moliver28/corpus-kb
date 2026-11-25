"""Typed source_types registry: canonical names, aliases, and one resolver.

The registry is application level. It never changes stored rows: a name the
registry does not know passes through ``canonical_source_type`` untouched, so
documents stored before this registry keep their legacy values. Third-party
types join through the ``corpus_kb.source_types`` entry-point group plus one
alias entry — no migration, mirroring the strategy registry.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP: str = "corpus_kb.source_types"


@dataclass(frozen=True)
class SourceTypeSpec:
    """One typed source: its canonical name, strategy, and aliases."""

    name: str
    strategy: str
    aliases: tuple[str, ...]
    metadata_keys: tuple[str, ...]
    text_ingest: bool
    description: str


BUILTIN_SOURCE_TYPES: tuple[SourceTypeSpec, ...] = (
    SourceTypeSpec(
        name="participant-session",
        strategy="transcript",
        aliases=("transcript",),
        metadata_keys=("participant_id", "study_id"),
        text_ingest=True,
        description="A research session transcript from one participant.",
    ),
    SourceTypeSpec(
        name="internal-meeting",
        strategy="markdown_sections",
        aliases=(),
        metadata_keys=(),
        text_ingest=True,
        description="Notes from an internal meeting, chunked by headings.",
    ),
    SourceTypeSpec(
        name="external-site",
        strategy="web_structure",
        aliases=("web",),
        metadata_keys=("visibility",),
        text_ingest=False,
        description="Pages from one website, ingested through the web pipeline.",
    ),
)

# Generic types stay valid and stored verbatim; they name no canonical target.
GENERIC_SOURCE_TYPES: frozenset[str] = frozenset({"code", "markdown", "text"})

# The one visibility vocabulary: callers declare public or internal on typed
# ingests, and the value stamps into document metadata for later filtering.
VISIBILITY_VALUES: frozenset[str] = frozenset({"public", "internal"})

_DISCOVERED: tuple[SourceTypeSpec, ...] | None = None


def _discover_entry_point_specs() -> tuple[SourceTypeSpec, ...]:
    """Load third-party source types from the entry-point group (error-tolerant).

    A broken plugin logs one warning and is skipped; it never takes ingest
    down (the tolerance of the strategy registry).
    """
    loaded: list[SourceTypeSpec] = []
    try:
        from importlib.metadata import entry_points

        plugins = entry_points(group=ENTRY_POINT_GROUP)
    except Exception as exc:  # metadata scan itself failed
        logger.warning("Source type entry-point scan failed: %s", exc)
        return tuple(loaded)
    taken = {spec.name for spec in BUILTIN_SOURCE_TYPES}
    for plugin in plugins:
        try:
            spec = plugin.load()
            if not isinstance(spec, SourceTypeSpec):
                raise TypeError("entry point must load a SourceTypeSpec")
            if not isinstance(spec.name, str) or not spec.name:
                raise ValueError("entry point spec needs a non-empty name")
            if spec.name in taken:
                raise ValueError(f"duplicate source type name {spec.name!r}")
            taken.add(spec.name)
            loaded.append(spec)
        except Exception as exc:
            logger.warning("Skipping broken source-type plugin %r: %s", plugin.name, exc)
    return tuple(loaded)


def _known_specs() -> tuple[SourceTypeSpec, ...]:
    """Builtins plus entry-point-discovered specs, discovered once per process."""
    global _DISCOVERED
    if _DISCOVERED is None:
        _DISCOVERED = _discover_entry_point_specs()
    return BUILTIN_SOURCE_TYPES + _DISCOVERED


def resolve_source_type(name: str) -> SourceTypeSpec | None:
    """The spec for a canonical name or an alias; None when unknown."""
    for spec in _known_specs():
        if name == spec.name or name in spec.aliases:
            return spec
    return None


def canonical_source_type(name: str) -> str | None:
    """The canonical name for a registry hit; every other non-empty name passes through.

    Generic types, unknown names, and strategy overrides return unchanged, so
    no stored value shifts under an existing row. An empty string returns None.
    """
    if not name:
        return None
    spec = resolve_source_type(name)
    return spec.name if spec is not None else name


def is_accepted_ingest_text_type(name: str) -> bool:
    """True when raw-text ingest accepts this type; False otherwise.

    Generic types pass. Registry types pass when their spec allows
    ``text_ingest`` (canonical or alias form). ``external-site`` and unknown
    names are refused.
    """
    if name in GENERIC_SOURCE_TYPES:
        return True
    spec = resolve_source_type(name)
    return spec.text_ingest if spec is not None else False
