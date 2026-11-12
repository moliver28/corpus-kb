"""Backend SQL variants for the #202 bulk re-embed (``ingest-reingest``).

One statement set per backend, chosen by ``reingest_sql_for`` (the same
per-engine split ``ingest_sql_text`` owns for the write pipeline). The
vector UPSERT itself is NOT here: the write goes through
``PostgresIngestStore.store_vectors`` (the ONE pipeline write path), so a
re-embed lands byte-identically to a fresh ingest. Only the bulk SCAN,
the plan counts, the width probe, and the ledger progress update are
backend-specific:

* Postgres casts uuid parameters (``$1::uuid``) and stores ``stages`` as
  ``jsonb``; DuckDB compares plain VARCHAR and casts ``JSON``.
* The keyset predicate ``(doc_id, chunk_id) > (floor, floor)`` gives the
  deterministic batch order (program standard 9) without OFFSET.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from corpus_kb.errors import ConfigError
from corpus_kb.storage.duckdb_support import Row

# --- The width probe: refuse BEFORE the ledger row opens -------------------
# The shipped Postgres column is halfvec(2560); DuckDB mirrors FLOAT[2560].
# A configured model emitting any other width CANNOT land, so the run must
# refuse up front with the typed ConfigError (program standard 4), not die
# at the first batch. Both probes are advisory: an unreadable catalog
# defers to store_vectors' own probe (the same deference ingest_sql uses).

_WIDTH_RE = re.compile(r"\((\d+)\)\s*$")
_FLOAT_ARR_RE = re.compile(r"FLOAT\[(\d+)\]", re.IGNORECASE)

WIDTH_PROBE_PG = (
    "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
    "WHERE a.attrelid = 'public.chunks_vectors'::regclass AND a.attname = 'embedding'"
)
WIDTH_PROBE_DUCKDB = "PRAGMA table_info('chunks_vectors')"


def width_from_type(typ: str | None) -> int | None:
    """The declared vector width from a catalog type text (or None)."""
    if not typ:
        return None
    match = _WIDTH_RE.search(typ)
    return int(match.group(1)) if match else None


def width_config_error(dimensions: int, width: int | None) -> ConfigError:
    """The typed refusal for a configured width the column cannot hold."""
    return ConfigError(
        f"Embedding width {dimensions} does not fit the embedding column "
        f"({width if width is not None else 'unknown'} dimensions stored). "
        "The engine needs an exact match.",
        remediation=(
            f"chunks_vectors.embedding stores exactly {width} dimensions. "
            f"Set embedding.model to a model that emits {width} dimensions, "
            f"set embedding.dimensions to {width}, then run "
            "corpus-kb ingest-reingest again."
        ),
    )


def duckdb_width_from_rows(rows: Sequence[Row]) -> int | None:
    """The FLOAT[N] width of ``embedding`` from ``PRAGMA table_info`` rows."""
    for row in rows:
        if row.get("name") == "embedding":
            match = _FLOAT_ARR_RE.fullmatch(str(row.get("type", "")))
            return int(match.group(1)) if match else None
    return None


# --- The scope predicate: tenant + tombstone + optional project ------------
# $1 tenant, $2 project (NULL = whole corpus), $3 target model, $4 dims.
# The LEFT JOIN carries the todo test; documents carries the project link.

_SCOPE = """
    FROM chunks c
    JOIN documents d ON d.doc_id = c.doc_id
    LEFT JOIN chunks_vectors cv ON cv.chunk_id = c.chunk_id
    WHERE c.tenant_id = {tenant} AND c.tombstoned_at IS NULL
      AND ({project} IS NULL OR d.project_id = {project})
"""

# The todo predicate: rows the target model has not already written well.
# IS DISTINCT FROM keeps NULL model/dimension rows in the todo set.
_TODO = (
    " AND (cv.chunk_id IS NULL OR cv.embedding IS NULL OR cv.degraded = TRUE"
    " OR c.degraded = TRUE"
    " OR cv.embedding_model IS DISTINCT FROM $3"
    " OR cv.dimensions IS DISTINCT FROM $4)"
)

_PG = {"tenant": "$1::uuid", "project": "$2::uuid"}
_DUCKDB = {"tenant": "$1", "project": "$2"}

_PLAN_DOCS_PG = "SELECT count(DISTINCT c.doc_id)" + _SCOPE.format(**_PG)
_PLAN_TOTAL_PG = "SELECT count(*)" + _SCOPE.format(**_PG)
_PLAN_TODO_PG = "SELECT count(*)" + _SCOPE.format(**_PG) + _TODO

# The dry-run size estimate: embed-input characters over the todo rows
# (blurb + blank line + text; the exact bytes build_embedding_input emits).
# fetchval reads the FIRST column, so this query selects the sum ONLY.
_EST_CHARS = (
    "SELECT coalesce(sum(char_length(c.text) + CASE WHEN c.context_blurb IS NULL"
    " THEN 0 ELSE char_length(c.context_blurb) + 2 END), 0)"
)
_PLAN_EST_PG = _EST_CHARS + _SCOPE.format(**_PG) + _TODO

_PLAN_DOCS_DUCKDB = "SELECT count(DISTINCT c.doc_id)" + _SCOPE.format(**_DUCKDB)
_PLAN_TOTAL_DUCKDB = "SELECT count(*)" + _SCOPE.format(**_DUCKDB)
_PLAN_TODO_DUCKDB = "SELECT count(*)" + _SCOPE.format(**_DUCKDB) + _TODO
_PLAN_EST_DUCKDB = _EST_CHARS + _SCOPE.format(**_DUCKDB) + _TODO

# --- The batch scan: deterministic keyset order, LIMIT-bounded -------------
# $5/$6 are the keyset floor (the zero uuid on the first page), $7 the cap.

_SCAN_PG = f"""
    SELECT c.chunk_id::text AS chunk_id, c.doc_id::text AS doc_id,
           c.text, c.context_blurb
{_SCOPE.format(**_PG)}{_TODO}
      AND (c.doc_id > $5::uuid OR (c.doc_id = $5::uuid AND c.chunk_id > $6::uuid))
    ORDER BY c.doc_id, c.chunk_id
    LIMIT $7
"""

_SCAN_DUCKDB = f"""
    SELECT c.chunk_id::text AS chunk_id, c.doc_id::text AS doc_id,
           c.text, c.context_blurb
{_SCOPE.format(**_DUCKDB)}{_TODO}
      AND (c.doc_id > $5 OR (c.doc_id = $5 AND c.chunk_id > $6))
    ORDER BY c.doc_id, c.chunk_id
    LIMIT $7
"""

# --- The ledger progress row (stages only; status stays 'running') --------

_PROGRESS_PG = (
    "UPDATE ingest_runs SET stages = $2::jsonb"
    " WHERE run_id = $1::uuid AND tenant_id = $3 AND status = 'running'"
)
_PROGRESS_DUCKDB = (
    "UPDATE ingest_runs SET stages = $2::JSON"
    " WHERE run_id = $1 AND tenant_id = $3 AND status = 'running'"
)


def reingest_sql_for(backend: str) -> dict[str, str]:
    """The per-backend bulk re-embed statements (postgres default)."""
    if backend == "duckdb":
        return {
            "plan_docs": _PLAN_DOCS_DUCKDB,
            "plan_total": _PLAN_TOTAL_DUCKDB,
            "plan_todo": _PLAN_TODO_DUCKDB,
            "plan_est": _PLAN_EST_DUCKDB,
            "scan": _SCAN_DUCKDB,
            "progress": _PROGRESS_DUCKDB,
        }
    return {
        "plan_docs": _PLAN_DOCS_PG,
        "plan_total": _PLAN_TOTAL_PG,
        "plan_todo": _PLAN_TODO_PG,
        "plan_est": _PLAN_EST_PG,
        "scan": _SCAN_PG,
        "progress": _PROGRESS_PG,
    }
