"""Re-export shim for the vector-search arms (issue #281 split).

``query_vector_arms.py`` held the arms, the SQL row helpers, and the
spine constants in one 420-line file. The split moved them to sibling
modules by arm family:

- ``query_vector_rows.py`` — shared SQL scaffolding and fetch helpers.
- ``query_vector_pgml_arm.py`` — the in-database pgml.embed arm.
- ``query_vector_ollama_arm.py`` — the local Ollama arm.

Import paths stay stable: every name this module exported before the
split still imports from here. New code should import from the sibling
modules directly.
"""

from __future__ import annotations

from corpus_kb.handlers.query_vector_ollama_arm import (
    _halfvec_rows,
    _matryoshka_rows,
    ollama_vector_arm,
)
from corpus_kb.handlers.query_vector_pgml_arm import (
    _pgml_exact_rows,
    _pgml_relaxed_rows,
    pgml_vector_arm,
)
from corpus_kb.handlers.query_vector_rows import (
    _CANDIDATE_SPINE,
    _COUNT_SQL_TEMPLATE,
    _CTE_SPINE,
    DEDUP_PREDICATE,
    PROVENANCE_COLS,
    SELECT_LIST,
    _candidate_limit,
    _run_in_savepoint,
    _use_exact_scan,
)

__all__ = [
    "DEDUP_PREDICATE",
    "PROVENANCE_COLS",
    "SELECT_LIST",
    "_CANDIDATE_SPINE",
    "_COUNT_SQL_TEMPLATE",
    "_CTE_SPINE",
    "_candidate_limit",
    "_halfvec_rows",
    "_matryoshka_rows",
    "_pgml_exact_rows",
    "_pgml_relaxed_rows",
    "_run_in_savepoint",
    "_use_exact_scan",
    "ollama_vector_arm",
    "pgml_vector_arm",
]
