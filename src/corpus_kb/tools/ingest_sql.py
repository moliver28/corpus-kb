"""Backend SQL variants for the ingest write layer (storage portability WP).

One :class:`IngestSql` per backend, chosen once by ``ingest_sql_for``. The
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

DOCUMENT_UPSERT_SQL = """
    INSERT INTO documents (doc_id, tenant_id, source, source_type,
        chunk_count, file_size, file_hash, metadata, document_date)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
    ON CONFLICT (tenant_id, source) DO UPDATE
    SET chunk_count = $5, file_size = $6, file_hash = $7,
        document_date = COALESCE(EXCLUDED.document_date, documents.document_date),
        updated_at = NOW()
    RETURNING doc_id::text
"""

# DuckDB cannot UPDATE a referenced parent row (an UPDATE rewrites the row,
# and eight child tables reference documents — engine limitation, duckdb#8988),
# so the DuckDB document "upsert" inserts once and afterwards only fetches
# the existing doc_id; run freshness lives in the ledger (migration 030
# adds ingest_runs.source).
DOCUMENT_INSERT_DUCKDB_SQL = """
    INSERT INTO documents (doc_id, tenant_id, source, source_type,
        chunk_count, file_size, file_hash, metadata, document_date)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
    ON CONFLICT (tenant_id, source) DO NOTHING
    RETURNING doc_id::text
"""

DOCUMENT_FETCH_DUCKDB_SQL = (
    "SELECT doc_id::text FROM documents WHERE tenant_id = $1 AND source = $2"
)

RUN_INSERT_SQL = """
    INSERT INTO ingest_runs (tenant_id, source_type, start_hash, status)
    VALUES ($1, $2, $3, 'running')
    RETURNING run_id::text
"""

RUN_INSERT_DUCKDB_SQL = """
    INSERT INTO ingest_runs (tenant_id, source, source_type, start_hash, status)
    VALUES ($1, $2, $3, $4, 'running')
    RETURNING run_id::text
"""

_ENTITY_UPSERT_SQL = """
    INSERT INTO entities (entity_id, tenant_id, name, entity_type,
        source_document_id, metadata)
    VALUES ($1, $2, $3, $4, $5, $6)
    ON CONFLICT (tenant_id, name, entity_type) DO UPDATE
        SET entity_id = entities.entity_id
    RETURNING entity_id
"""

# DuckDB: an ON CONFLICT DO UPDATE rewrites the row, and relations reference
# entities, so the dedupe inserts-or-fetches instead of rewriting.
_ENTITY_INSERT_DUCKDB_SQL = """
    INSERT INTO entities (entity_id, tenant_id, name, entity_type,
        source_document_id, metadata)
    VALUES ($1, $2, $3, $4, $5, $6)
    ON CONFLICT (tenant_id, name, entity_type) DO NOTHING
    RETURNING entity_id
"""

_ENTITY_FETCH_DUCKDB_SQL = (
    "SELECT entity_id::text AS entity_id FROM entities "
    "WHERE tenant_id = $1 AND name = $2 AND entity_type = $3"
)


PG_VECTOR_TYPEMOD_SQL = (
    "SELECT a.atttypmod AS width, format_type(a.atttypid, a.atttypmod) AS typ "
    "FROM pg_attribute a "
    "WHERE a.attrelid = 'public.chunks_vectors'::regclass AND a.attname = 'embedding'"
)

_UNCHANGED_TAIL_PG = """
    FROM documents d
    WHERE d.tenant_id = $1 AND d.source = $2 AND d.file_hash = $3
      AND EXISTS (
          SELECT 1 FROM ingest_runs r
          WHERE r.tenant_id = d.tenant_id
            AND r.start_hash = d.file_hash
            AND r.status = 'completed'
            AND r.stages->'errors' = '[]'::jsonb
      )
"""

# DuckDB: the documents row is immutable once referenced, so "this content
# is current" means the NEWEST run for this source started with this hash
# and completed clean (migration 030 adds ingest_runs.source).
UNCHANGED_DOC_DUCKDB_SQL = """
    SELECT d.doc_id::text AS doc_id
    FROM documents d
    WHERE d.tenant_id = $1 AND d.source = $2
      AND EXISTS (
          SELECT 1 FROM ingest_runs r
          WHERE r.tenant_id = d.tenant_id
            AND r.source = d.source
            AND r.start_hash = $3
            AND r.status = 'completed'
            AND (r.stages->'errors') = '[]'::JSON
            AND NOT EXISTS (
                SELECT 1 FROM ingest_runs newer
                WHERE newer.tenant_id = r.tenant_id
                  AND newer.source = r.source
                  AND (newer.started_at, newer.run_id) > (r.started_at, r.run_id)
            )
      )
"""

_FINISH_TAIL = """
    UPDATE ingest_runs SET
        status = $2,
        finished_at = NOW(),
        finish_hash = $3,
        stages = {stages_cast},
        error = $5
    WHERE run_id = {run_ref} AND tenant_id = $6
"""

_VECTOR_INSERT = """
    INSERT INTO chunks_vectors (chunk_id, tenant_id, embedding, vector_1024,
        embedding_model, dimensions, degraded)
    VALUES ($1, $2, {embedding}, {v1024}, $5, $6, $7)
    ON CONFLICT (chunk_id) DO UPDATE
    SET embedding = {embedding},
        vector_1024 = {v1024},
        embedding_model = $5, dimensions = $6, embedded_at = NOW(),
        degraded = $7
"""


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


# --- Reembed repair (the Stage-3 degraded/stale scan + write) ---
COLUMNS_PROBE_PG = (
    "SELECT count(*) FROM information_schema.columns "
    "WHERE table_schema = 'public' AND table_name = ANY($1::text[]) "
    "AND column_name = 'degraded'"
)
COLUMNS_PROBE_DUCKDB = (
    "SELECT count(*) FROM duckdb_columns() WHERE table_name IN "
    "('chunks', 'chunks_vectors') AND column_name = 'degraded'"
)

SCAN_SQL_PG = """
    SELECT cv.chunk_id::text AS chunk_id, c.doc_id::text AS doc_id,
           c.text, c.context_blurb,
           (cv.embedding IS NULL) AS was_stale
    FROM chunks_vectors cv
    JOIN chunks c ON c.chunk_id = cv.chunk_id
    WHERE cv.tenant_id = $1
      AND (cv.degraded = TRUE OR c.degraded = TRUE OR cv.embedding IS NULL)
      AND ($2::uuid IS NULL OR c.doc_id = $2::uuid)
    ORDER BY cv.embedded_at NULLS LAST
    LIMIT $3
"""
SCAN_SQL_DUCKDB = """
    SELECT cv.chunk_id::text AS chunk_id, c.doc_id::text AS doc_id,
           c.text, c.context_blurb,
           (cv.embedding IS NULL) AS was_stale
    FROM chunks_vectors cv
    JOIN chunks c ON c.chunk_id = cv.chunk_id
    WHERE cv.tenant_id = $1
      AND (cv.degraded = TRUE OR c.degraded = TRUE OR cv.embedding IS NULL)
      AND ($2::VARCHAR IS NULL OR c.doc_id = $2::VARCHAR)
    ORDER BY cv.embedded_at NULLS LAST
    LIMIT $3
"""

REPAIR_SQL_PG = """
    UPDATE chunks_vectors
    SET embedding = $2::halfvec,
        vector_1024 = COALESCE($3::vector, chunks_vectors.vector_1024),
        embedding_model = $4, dimensions = $5,
        embedded_at = NOW(), degraded = FALSE
    WHERE chunk_id = $1::uuid AND tenant_id = $6
"""
REPAIR_SQL_DUCKDB = """
    UPDATE chunks_vectors
    SET embedding = $2::FLOAT[2560],
        vector_1024 = COALESCE(CAST($3 AS FLOAT[1024]), chunks_vectors.vector_1024),
        embedding_model = $4, dimensions = $5,
        embedded_at = NOW(), degraded = FALSE
    WHERE chunk_id = $1 AND tenant_id = $6
"""
DEGRADED_CLEAR_PG = (
    "UPDATE chunks SET degraded = FALSE WHERE chunk_id = $1::uuid AND tenant_id = $2"
)
DEGRADED_CLEAR_DUCKDB = "UPDATE chunks SET degraded = FALSE WHERE chunk_id = $1 AND tenant_id = $2"
