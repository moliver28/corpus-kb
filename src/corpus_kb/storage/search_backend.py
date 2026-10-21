"""SearchBackend protocol: the engine-split seam for hybrid search.

Storage-portability program §3.3: one method — ``search(SearchRequest) ->
SearchOutcome``. The protocol lives in ``storage/`` so implementations
import downward (zero-drift layering rule 5: handlers never import
drivers). QueryHandler keeps orchestration and the engine-neutral RRF
fusion; a backend carries the engine split of the arms (dense vector,
lexical, degraded probe).

Honesty contract (§6): every outcome states which index mode served the
search (``vector_index_mode`` / ``lexical_index_mode`` — guaranteed
envelope keys, see ``contracts/envelopes.py``) and may attach a note.
Notes are conditional and follow the ``embedding_mode``/``embedding_note``
shape: a note appears only when the mode is not the engine's default.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import asyncpg

from corpus_kb.domain.models import SearchQuery
from corpus_kb.rag.self_query import Predicate

# The arm rows the fusion and the provenance re-attach consume. Postgres
# arms return asyncpg.Record rows; other engines return plain dicts. Both
# support ``row["column"]`` access (the program's Row convention, §3.1).
SearchRow = asyncpg.Record | dict[str, Any]


@dataclass(frozen=True)
class SearchRequest:
    """One connection-scoped search-arm request.

    ``conn`` is the caller's live connection inside the handler's
    transaction (the tenant GUC is already set), so the arms run on the
    SAME transaction and a failed arm stays isolated by its savepoint.
    ``reasons`` is the shared impairment list: the backend appends arm
    impairments (zero-query embedding, arm errors, filter-empty) exactly
    like the pre-split inline pipeline.
    """

    conn: asyncpg.Connection
    query: SearchQuery
    semantic_query: str
    dynamic_predicates: list[Predicate]
    degraded_pred: str
    over_retrieve_n: int
    reasons: list[str]


@dataclass(frozen=True)
class SearchOutcome:
    """Raw arm rows plus the honesty metadata of one backend run.

    ``vector_rows`` / ``lexical_rows`` are the arm candidates with their
    arm scores; QueryHandler owns the engine-neutral RRF fusion over them.
    ``excluded_count`` is the degraded-probe count (0 when no probe ran).
    ``vector_index_mode`` / ``lexical_index_mode`` are the guaranteed
    honesty keys; the notes are conditional (never on a default mode).
    """

    vector_rows: Sequence[SearchRow]
    lexical_rows: Sequence[SearchRow]
    excluded_count: int
    vector_index_mode: str
    lexical_index_mode: str
    vector_index_note: str | None = None
    lexical_index_note: str | None = None


@runtime_checkable
class SearchBackend(Protocol):
    """The engine split of the search arms (one method)."""

    async def search(self, request: SearchRequest) -> SearchOutcome:
        """Run the dense + lexical arms and the degraded probe on one connection."""
        ...
