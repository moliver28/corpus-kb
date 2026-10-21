"""Backend SQL variants for the ingest write layer (storage portability WP).

One :class:`IngestSql` per backend, chosen once by ``ingest_sql_for``. The
statement TEXTS live in :mod:`ingest_sql_text` (a pure move for the
250-line rule; every string is byte-identical to its pre-split text). The
Postgres statements are byte-identical to the pre-port SQL text; the DuckDB
statements differ ONLY where the engines differ, each verified on duckdb
1.4.5 (findings/sql-typing.md, findings/storage-ops.md):

* ``jsonb`` is ``JSON`` on DuckDB, and ``stages->'errors'`` needs explicit
  parentheses (DuckDB binds ``=`` tighter than the arrow operator).
* ``run_id`` is ``uuid`` on Postgres and ``varchar`` on DuckDB, so the
  ledger updates cast the parameter only on Postgres.
* Vectors: Postgres takes a ``"[...]"`` literal into ``halfvec``/``vector``
  casts; DuckDB takes ONE Python list per array column (never per-element
  placeholders), and a wrong-width list fails with a parseable
  ``ConversionException`` ("Cannot cast list with length N to array with
  length M") — translated to the same typed ConfigError as pgvector's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..errors import ConfigError
from ..storage.duckdb_support import vector_literal
from ..storage.gateway import SqlGateway
from .ingest_sql_text import (
    _ENTITY_FETCH_DUCKDB_SQL,
    _ENTITY_INSERT_DUCKDB_SQL,
    _ENTITY_UPSERT_SQL,
    _FINISH_TAIL,
    _UNCHANGED_TAIL_PG,
    _VECTOR_INSERT,
    COLUMNS_PROBE_DUCKDB,
    COLUMNS_PROBE_PG,
    DEGRADED_CLEAR_DUCKDB,
    DEGRADED_CLEAR_PG,
    DOCUMENT_FETCH_DUCKDB_SQL,
    DOCUMENT_INSERT_DUCKDB_SQL,
    DOCUMENT_UPSERT_SQL,
    PG_VECTOR_TYPEMOD_SQL,
    REPAIR_SQL_DUCKDB,
    REPAIR_SQL_PG,
    RUN_INSERT_DUCKDB_SQL,
    RUN_INSERT_SQL,
    SCAN_SQL_DUCKDB,
    SCAN_SQL_PG,
    UNCHANGED_DOC_DUCKDB_SQL,
)

__all__ = [
    "COLUMNS_PROBE_DUCKDB",
    "COLUMNS_PROBE_PG",
    "DEGRADED_CLEAR_DUCKDB",
    "DEGRADED_CLEAR_PG",
    "DOCUMENT_FETCH_DUCKDB_SQL",
    "DOCUMENT_INSERT_DUCKDB_SQL",
    "DOCUMENT_UPSERT_SQL",
    "PG_VECTOR_TYPEMOD_SQL",
    "REPAIR_SQL_DUCKDB",
    "REPAIR_SQL_PG",
    "RUN_INSERT_DUCKDB_SQL",
    "RUN_INSERT_SQL",
    "SCAN_SQL_DUCKDB",
    "SCAN_SQL_PG",
    "UNCHANGED_DOC_DUCKDB_SQL",
    "IngestSql",
    "duckdb_vector_width_probe",
    "ingest_sql_for",
    "pg_vector_type_mod_probe",
    "vector_width_config_error",
    "vector_write_params",
]


@dataclass(frozen=True)
class IngestSql:
    """The per-backend ingest statements (see module docstring)."""

    backend: str
    unchanged_doc_sql: str
    document_insert_sql: str
    document_fetch_sql: str
    run_insert_sql: str
    entity_insert_sql: str
    entity_fetch_sql: str
    finish_run_sql: str
    fail_run_sql: str
    vector_insert_sql: str

    @property
    def is_duckdb(self) -> bool:
        return self.backend == "duckdb"


def ingest_sql_for(backend: str) -> IngestSql:
    """Return the ingest statements for ``backend`` (postgres default)."""
    if backend == "duckdb":
        return IngestSql(
            backend=backend,
            unchanged_doc_sql=UNCHANGED_DOC_DUCKDB_SQL,
            document_insert_sql=DOCUMENT_INSERT_DUCKDB_SQL,
            document_fetch_sql=DOCUMENT_FETCH_DUCKDB_SQL,
            run_insert_sql=RUN_INSERT_DUCKDB_SQL,
            entity_insert_sql=_ENTITY_INSERT_DUCKDB_SQL,
            entity_fetch_sql=_ENTITY_FETCH_DUCKDB_SQL,
            finish_run_sql=_FINISH_TAIL.format(stages_cast="$4::JSON", run_ref="$1"),
            fail_run_sql=(
                "UPDATE ingest_runs SET status = 'failed', finished_at = NOW(), error = $2"
                " WHERE run_id = $1 AND tenant_id = $3"
            ),
            vector_insert_sql=_VECTOR_INSERT.format(
                embedding="$3::FLOAT[2560]", v1024="$4::FLOAT[1024]"
            ),
        )
    return IngestSql(
        backend=backend,
        unchanged_doc_sql=("SELECT d.doc_id::text AS doc_id" + _UNCHANGED_TAIL_PG),
        document_insert_sql=DOCUMENT_UPSERT_SQL,
        document_fetch_sql=DOCUMENT_FETCH_DUCKDB_SQL,  # unused on Postgres
        run_insert_sql=RUN_INSERT_SQL,
        entity_insert_sql=_ENTITY_UPSERT_SQL,
        entity_fetch_sql=_ENTITY_FETCH_DUCKDB_SQL,  # unused on Postgres
        finish_run_sql=_FINISH_TAIL.format(stages_cast="$4::jsonb", run_ref="$1::uuid"),
        fail_run_sql=(
            "UPDATE ingest_runs SET status = 'failed', finished_at = NOW(), error = $2"
            " WHERE run_id = $1::uuid AND tenant_id = $3"
        ),
        vector_insert_sql=_VECTOR_INSERT.format(embedding="$3::halfvec", v1024="$4::vector"),
    )


_PG_WIDTH_RE = re.compile(r"expected (\d+) dimensions, not (\d+)")
_DUCKDB_WIDTH_RE = re.compile(r"list with length (\d+) to array with length (\d+)")


def vector_width_config_error(exc: BaseException, embedding_model: str) -> ConfigError:
    """Translate a width-mismatch write error into the typed ConfigError.

    Matches pgvector's "expected N dimensions, not M" (Postgres DataError)
    and DuckDB's "Cannot cast list with length N to array with length M".
    Re-raises ``exc`` unchanged when neither matches.
    """
    match = _PG_WIDTH_RE.search(str(exc)) or _DUCKDB_WIDTH_RE.search(str(exc))
    if match is None:
        raise exc
    expected, actual = int(match.group(1)), int(match.group(2))
    return ConfigError(
        f"Embedding width {actual} does not fit the embedding column "
        f"(model {embedding_model!r}). The engine needs an exact match.",
        remediation=(
            f"chunks_vectors.embedding stores exactly {expected} dimensions. Set "
            f"embedding.model to a model that emits {expected} dimensions, set "
            f"embedding.dimensions to {expected}, then re-ingest."
        ),
    )


def vector_write_params(
    embedding: list[float], v1024: list[float] | None
) -> tuple[str, str | None]:
    """The (embedding, vector_1024) bind values: COMPACT array literals.

    Both engines take the canonical ``"[...]"`` literal
    (:func:`duckdb_support.vector_literal`, no internal spaces) that the
    SQL's explicit cast parses; binding a Python list into a DuckDB
    fixed-size array costs seconds per statement (sp-duckdb-search W0
    bench: 2.7s per bind vs ~10ms for the literal+cast form).
    """
    embedding_str = vector_literal(embedding)
    v1024_str = vector_literal(v1024) if v1024 else None
    return embedding_str, v1024_str


async def pg_vector_type_mod_probe(gw: SqlGateway, dimensions: int, embedding_model: str) -> None:
    """Postgres width guard: probe the pgvector column typmod once (advisory)."""
    try:
        row = await gw.fetchrow(PG_VECTOR_TYPEMOD_SQL)
    except Exception:
        return
    if row is None:
        return
    width = int(row["width"])
    if width < 0 or width == dimensions:
        return
    raise ConfigError(
        f"Embedding width {dimensions} does not fit the embedding column {row['typ']}. "
        "pgvector needs an exact match. Every vector insert will fail.",
        remediation=(
            f"chunks_vectors.embedding stores exactly {width} dimensions. Set "
            f"embedding.model to a model that emits {width} dimensions, set "
            f"embedding.dimensions to {width}, then re-ingest."
        ),
    )


async def duckdb_vector_width_probe(gw: SqlGateway, dimensions: int, embedding_model: str) -> None:
    """DuckDB width guard: parse ``FLOAT[N]`` from ``PRAGMA table_info``.

    Conversion errors ABORT a DuckDB transaction, so the width is checked
    BEFORE the insert. The probe is advisory: an unreadable catalog defers
    to the INSERT's own error.
    """
    try:
        rows = await gw.fetch("PRAGMA table_info('chunks_vectors')")
    except Exception:
        return
    column = next((row for row in rows if row.get("name") == "embedding"), None)
    if column is None:
        return
    match = re.fullmatch(r"FLOAT\[(\d+)\]", str(column.get("type", "")))
    if match is None:
        return
    width = int(match.group(1))
    if width == dimensions:
        return
    raise ConfigError(
        f"Embedding width {dimensions} does not fit the embedding column "
        f"{column.get('type')}. DuckDB needs an exact match. Every vector "
        "insert will fail.",
        remediation=(
            f"chunks_vectors.embedding stores exactly {width} dimensions. Set "
            f"embedding.model to a model that emits {width} dimensions, set "
            f"embedding.dimensions to {width}, then re-ingest."
        ),
    )
