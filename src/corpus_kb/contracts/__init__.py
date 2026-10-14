"""Operation contracts: the catalogue the parity gate and the inventory share.

Read ``contracts.model`` for the row types, ``contracts.catalogue`` +
``contracts.catalogue_research`` for the rows, and ``contracts.openapi``
for the HTTP spec generator.
"""

from __future__ import annotations

from corpus_kb.contracts.catalogue_research import (
    OPERATIONS,
    SKIPPED_OPERATIONS,
    get_operation,
)
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
