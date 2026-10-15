"""Operation contracts: the catalogue the parity gate and the inventory share.

Row types live in ``contracts.model``, the shared envelope tuples in
``contracts.envelopes``, and the rows themselves in
``contracts.catalogue`` (ingest + search), ``contracts.catalogue_documents``
(documents + graph), ``contracts.catalogue_release`` (release lifecycle),
``contracts.catalogue_research`` (research rows + the SKIP set), and
``contracts.catalogue_topics`` (the Wave 4 topics family). This
package assembles the FINAL catalogue: ``OPERATIONS`` is
the tuple every consumer (the parity gate, ``corpus-kb operations``)
iterates, so the test and the inventory can never diverge.
"""

from __future__ import annotations

from corpus_kb.contracts.catalogue import CORE_OPERATIONS
from corpus_kb.contracts.catalogue_documents import DOCUMENTS_OPERATIONS
from corpus_kb.contracts.catalogue_release import RELEASE_OPERATIONS
from corpus_kb.contracts.catalogue_research import (
    RESEARCH_OPERATIONS,
    SKIPPED_OPERATIONS,
)
from corpus_kb.contracts.catalogue_topics import TOPICS_OPERATIONS
from corpus_kb.contracts.model import (
    DEFAULT_TENANT_ID,
    ERROR_MAP,
    Operation,
    ParamSpec,
    SkippedOperation,
    SurfaceSpec,
    error_triplet,
    surfaces_of,
)

OPERATIONS: tuple[Operation, ...] = (
    *CORE_OPERATIONS,
    *DOCUMENTS_OPERATIONS,
    *RELEASE_OPERATIONS,
    *RESEARCH_OPERATIONS,
    *TOPICS_OPERATIONS,
)

__all__ = [
    "DEFAULT_TENANT_ID",
    "ERROR_MAP",
    "OPERATIONS",
    "SKIPPED_OPERATIONS",
    "Operation",
    "ParamSpec",
    "SkippedOperation",
    "SurfaceSpec",
    "error_triplet",
    "get_operation",
    "surfaces_of",
]


def get_operation(name: str) -> Operation | None:
    """Return the catalogue row for one operation name, or None."""
    for op in OPERATIONS:
        if op.name == name:
            return op
    return None
