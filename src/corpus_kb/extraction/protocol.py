"""Pluggable ontology extractor protocol, shared exceptions, and stats.

``ExtractStats`` is the honest-counting contract shared across the ingest
pipeline: every backend reports what it was asked to keep versus what it
actually kept, and where the difference went (cap, confidence threshold,
other). The ingest result dict surfaces these counts verbatim — an
extractor must never report success with numbers it did not measure.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..ontology import Ontology
from ..utils.models import Chunk, Entity, Relation


class OntologyViolationError(Exception):
    """Raised when an entity or relation type is not declared in the ontology."""

    def __init__(self, kind: str, value: str, allowed: list[str]) -> None:
        message = f"Ontology violation: {kind} '{value}' not in {allowed}"
        super().__init__(message)
        self.kind = kind
        self.value = value
        self.allowed = allowed


@dataclass(frozen=True, slots=True)
class ExtractStats:
    """Measured outcome of one extraction run.

    Attributes:
        backend: ``extractor_id`` of the backend that produced the run.
        requested_entities: Entity candidates the backend produced before
            any filtering.
        kept_entities: Entities actually returned.
        requested_relations: Relation candidates the backend produced
            before any filtering.
        kept_relations: Relations actually returned.
        dropped_by_cap: Candidates dropped by a max-per-chunk cap
            (relations only today).
        dropped_by_confidence: Candidates dropped for scoring below the
            backend's confidence threshold.
        dropped_by_other: Candidates dropped for any other reason
            (invalid offsets, unresolved relation endpoints, ...).
        duration_ms: Wall-clock time of the extraction run.
        notes: Free-text honesty notes (failure records, capability
            limitations such as gliner's missing relation extraction).
    """

    backend: str
    requested_entities: int = 0
    kept_entities: int = 0
    requested_relations: int = 0
    kept_relations: int = 0
    dropped_by_cap: int = 0
    dropped_by_confidence: int = 0
    dropped_by_other: int = 0
    duration_ms: float = 0.0
    notes: tuple[str, ...] = field(default=())

    @property
    def dropped_entities(self) -> int:
        """Entities requested minus entities kept."""
        return self.requested_entities - self.kept_entities

    @property
    def dropped_relations(self) -> int:
        """Relations requested minus relations kept."""
        return self.requested_relations - self.kept_relations


class Timer:
    """perf_counter stopwatch for one extraction run.

    Usage::

        timer = Timer("gliner")
        ... extract ...
        stats = ExtractStats(
            backend=timer.backend, duration_ms=timer.elapsed_ms(), ...
        )
    """

    def __init__(self, backend: str) -> None:
        self.backend = backend
        self._start = time.perf_counter()

    def elapsed_ms(self) -> float:
        """Milliseconds elapsed since the Timer was constructed."""
        return (time.perf_counter() - self._start) * 1000.0


@runtime_checkable
class Extractor(Protocol):
    """Protocol for pluggable ontology-driven extractors."""

    extractor_id: str

    def extract(
        self,
        chunks: list[Chunk],
        ontology: Ontology,
        source_document_id: str,
    ) -> tuple[list[Entity], list[Relation], ExtractStats]:
        """Extract entities and typed relations from chunks.

        Args:
            chunks: Text chunks to process.
            ontology: Ontology constraining allowed entity/relation types.
            source_document_id: Document these chunks belong to.

        Returns:
            Tuple of (entities, relations, stats). ``stats`` must report
            requested versus kept counts honestly (see :class:`ExtractStats`).

        Raises:
            OntologyViolationError: If any emitted type is not in the ontology.
        """
        raise NotImplementedError
