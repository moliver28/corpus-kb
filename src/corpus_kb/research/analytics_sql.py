"""SQL-side kNN analytics on the 016 embedding_256 column (todo 17, r7/r10).

ALL kNN analytics run inside the database via the pinned HNSW probe
``embedding_256 <=> $1::vector(256)`` (tenant-scoped through the tenant GUC
+ FORCE RLS) — never Python-side O(n·m) full-vector scans. Per the MRL
contract (Kusupati et al. 2022; r10): the 256-d HNSW scan RETRIEVES the
candidate top-k id sets (one ordered scan per prototype), then surfaced
candidates are RE-RANKED at the FULL 1024-d vectors by exact cosine
(trivial reduction over the retrieved rows — ids fetched by PK, never a
table scan). Final residual/ranking values are therefore full-dim exact,
consistent with the 1024-d tau_res calibration. Recall@k delta of the
256-d slice vs full-dim is 0.0 on the todo-13 G1 artifact (MRL report).

The retrieval leg uses the halfvec(1024) cast index with the pinned probe
``embedding::halfvec(1024) <=> $1::halfvec(1024)`` — one primary index per
access pattern (r11); HALFVEC_PROBE_SQL exists for the EXPLAIN proof.

U40 (P3): every analytics KNN read follows the relaxed-ordering pattern —
the ANN scan (filters + distance ORDER BY + LIMIT) stays INSIDE a
``WITH candidate AS MATERIALIZED`` CTE and the exact ordering is applied
OUTSIDE over the retrieved set (``dist + 0`` defeats planner sort-elision,
per the pgvector docs). These reads pull fixed-shaped candidate sets (one
ordered scan per prototype), so the materialization costs nothing extra.

Zero-drift rule 9: each outer ordering also carries the stable-id
tiebreaker (``ORDER BY <dist>, <id> ASC``), the same contract the search
arms pin in tests/test_query_determinism.py. Tied distances then keep one
output order on every run and every prototype sweep.

``explain_plan`` captures the EXPLAIN text so the fixture can assert index
usage. ``force_index=True`` emits ``SET LOCAL enable_seqscan = off`` inside
an explicit transaction (autocommit would drop the SET LOCAL) — on
fixture-sized tables the btree+sort plan is otherwise cheaper; production
leaves the planner free.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np

from corpus_kb.dimensions import RESEARCH_EMBEDDING_DIMENSIONS

RETRIEVAL_UNITS_SQL = """
WITH candidate AS MATERIALIZED (
    SELECT ru.unit_id,
           ru.embedding_256 <=> $1::vector(256) AS dist
    FROM research_units ru
    WHERE ru.is_codable AND ru.role_in_exchange = 'answer'
      AND ru.embedding_256 IS NOT NULL
    ORDER BY ru.embedding_256 <=> $1::vector(256)
    LIMIT $2
)
SELECT candidate.unit_id
FROM candidate
ORDER BY candidate.dist + 0, candidate.unit_id ASC
"""

RETRIEVAL_UNITS_TYPED_SQL = """
WITH candidate AS MATERIALIZED (
    SELECT ru.unit_id,
           ru.embedding_256 <=> $1::vector(256) AS dist
    FROM research_units ru
    JOIN documents d ON d.doc_id = ru.doc_id
    WHERE ru.is_codable AND ru.role_in_exchange = 'answer'
      AND ru.embedding_256 IS NOT NULL
      AND d.source_type = $2::text
    ORDER BY ru.embedding_256 <=> $1::vector(256)
    LIMIT $3
)
SELECT candidate.unit_id
FROM candidate
ORDER BY candidate.dist + 0, candidate.unit_id ASC
"""

RETRIEVAL_EXCHANGES_SQL = """
WITH candidate AS MATERIALIZED (
    SELECT e.exchange_id,
           e.embedding_256 <=> $1::vector(256) AS dist
    FROM research_exchanges e
    WHERE e.embedding_256 IS NOT NULL
    ORDER BY e.embedding_256 <=> $1::vector(256)
    LIMIT $2
)
SELECT candidate.exchange_id
FROM candidate
ORDER BY candidate.dist + 0, candidate.exchange_id ASC
"""

RETRIEVAL_UNCODED_SQL = """
WITH candidate AS MATERIALIZED (
    SELECT ru.unit_id,
           ru.embedding_256 <=> $1::vector(256) AS dist
    FROM research_units ru
    WHERE ru.is_codable AND ru.role_in_exchange = 'answer'
      AND ru.embedding_256 IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM research_assignments ra
          WHERE ra.tenant_id = ru.tenant_id AND ra.unit_id = ru.unit_id
      )
    ORDER BY ru.embedding_256 <=> $1::vector(256)
    LIMIT $2
)
SELECT candidate.unit_id
FROM candidate
ORDER BY candidate.dist + 0, candidate.unit_id ASC
"""

HALFVEC_PROBE_SQL = """
WITH candidate AS MATERIALIZED (
    SELECT ru.unit_id,
           ru.embedding::halfvec(1024) <=> $1::halfvec(1024) AS dist
    FROM research_units ru
    WHERE ru.is_codable AND ru.role_in_exchange = 'answer' AND ru.embedding IS NOT NULL
    ORDER BY ru.embedding::halfvec(1024) <=> $1::halfvec(1024)
    LIMIT $2
)
SELECT candidate.unit_id
FROM candidate
ORDER BY candidate.dist + 0, candidate.unit_id ASC
"""

# The probe is a pinned EXPLAIN-proof artifact (module docstring, r11), so
# the width stays a literal. The assert ties it to the shared constant: a
# width change fails here at import, not in a fixture.
assert f"halfvec({RESEARCH_EMBEDDING_DIMENSIONS})" in HALFVEC_PROBE_SQL

UNIT_VECTORS_SQL = """
SELECT unit_id, embedding FROM research_units WHERE unit_id = ANY($1::bigint[])
"""

UNIT_TEXT_VECTORS_SQL = """
SELECT unit_id, text, embedding FROM research_units WHERE unit_id = ANY($1::bigint[])
"""

EXCHANGE_VECTORS_SQL = """
SELECT exchange_id, embedding FROM research_exchanges
WHERE exchange_id = ANY($1::bigint[])
"""

EXCHANGE_ANSWER_UNITS_SQL = """
SELECT unit_id, exchange_id FROM research_units
WHERE exchange_id = ANY($1::bigint[]) AND role_in_exchange = 'answer' AND is_codable
"""

_FETCH_CHUNK = 500


def mrl_256(vector: Sequence[float]) -> list[float]:
    """256-d MRL slice of a 1024-d vector, L2-renormalized."""
    arr = np.asarray(vector[:256], dtype=np.float32)
    if len(arr) < 256:
        raise ValueError(f"prototype has {len(arr)} dims; need >= 256 for the MRL slice")
    norm = float(np.linalg.norm(arr))
    if norm == 0.0:
        raise ValueError("zero prototype has no MRL slice")
    return (arr / norm).tolist()


def probe_256(vector: Sequence[float]) -> str:
    """256-d MRL probe literal for the analytics HNSW scan."""
    return "[" + ",".join(f"{x:.9g}" for x in mrl_256(vector)) + "]"


def _parse_vector(raw: Any) -> list[float]:
    """PG vector text form ('[a,b,...]'); DuckDB list reads pass through."""
    if isinstance(raw, (list, tuple)):
        return [float(v) for v in raw]
    text = str(raw).strip().strip("[]")
    return [float(x) for x in text.split(",")] if text and text != "None" else []


# The public runners live in analytics_aggregates.py (the split keeps this
# file the pinned-SQL home); the names re-export lazily (PEP 562) so every
# caller import (notebook_views, calibration, the fixtures) stays as-is.
_PUBLIC_RUNNERS = frozenset(
    {"residual_scan", "residual_scan_exchanges", "uncoded_units_ranking", "explain_plan"}
)


def __getattr__(name: str) -> Any:
    if name in _PUBLIC_RUNNERS:
        from corpus_kb.research import analytics_runners

        return getattr(analytics_runners, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
