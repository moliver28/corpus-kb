"""S4 spike 3: live-PG DuckDB ATTACH probe. READ-ONLY against the server.

1. asyncpg: report PG-side column types for chunks_vectors + sample JSONB /
   NUMERIC / uuid columns (information_schema reads only).
2. DuckDB: ATTACH the live DB as TYPE POSTGRES, READ_ONLY; DESCRIBE the
   attached tables to record DuckDB-side type mappings.
3. Scratch DuckDB file: CREATE TABLE AS SELECT (LIMIT 5) from chunks;
   verify constraints dropped and halfvec text parseable.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import asyncpg


def _ident(name: str) -> str:
    """Pass plain SQL identifiers; refuse everything else."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""):
        raise SystemExit(f"unsafe SQL identifier: {name!r}")
    return name


DSN = "postgresql://postgres:postgres@localhost:5432/corpus_kb"
SCRATCH = Path(__file__).resolve().parent / "scratch"
SCRATCH.mkdir(exist_ok=True)
SCRATCH_DB = SCRATCH / "attach_ctas.duckdb"

PG_TYPE_SQL = """
SELECT table_schema, table_name, column_name, data_type, udt_name
FROM information_schema.columns
WHERE table_schema NOT IN ('pg_catalog','information_schema')
  AND (udt_name IN ('halfvec','vector','jsonb') OR data_type = 'numeric'
       OR (data_type='numeric' AND numeric_precision IS NULL))
ORDER BY table_name, ordinal_position
LIMIT 80
"""

CHUNKS_COLS_SQL = """
SELECT column_name, data_type, udt_name, numeric_precision, numeric_scale
FROM information_schema.columns
WHERE table_schema='public' AND table_name='chunks_vectors'
ORDER BY ordinal_position
"""


async def pg_side() -> dict:
    conn = await asyncpg.connect(DSN, timeout=5)
    try:
        cv = await conn.fetch(CHUNKS_COLS_SQL)
        interesting = await conn.fetch(PG_TYPE_SQL)
        # unbounded numerics specifically
        unbounded = await conn.fetch(
            """
            SELECT table_name, column_name FROM information_schema.columns
            WHERE table_schema='public' AND data_type='numeric'
              AND numeric_precision IS NULL LIMIT 20
            """
        )
        return {
            "chunks_vectors_pg_columns": [dict(r) for r in cv],
            "jsonb_vector_cols_sample": [dict(r) for r in interesting][:40],
            "unbounded_numeric": [dict(r) for r in unbounded],
        }
    finally:
        await conn.close()


def duck_side() -> dict:
    import duckdb

    out: dict = {}
    con = duckdb.connect()
    con.execute("LOAD postgres")
    if "'" in DSN or "\x00" in DSN:
        raise SystemExit("DSN must not contain quotes or NUL bytes")
    con.execute("ATTACH '" + DSN.replace("'", "''") + "' AS src (TYPE POSTGRES, READ_ONLY)")
    # DuckDB-side types of chunks_vectors
    out["chunks_vectors_duckdb_types"] = [
        [r[0], r[1]] for r in con.execute("DESCRIBE src.public.chunks_vectors").fetchall()
    ]
    out["documents_duckdb_types"] = [
        [r[0], r[1]] for r in con.execute("DESCRIBE src.public.documents").fetchall()
    ]
    # a JSONB column (code_registry.examples) and unbounded numeric (code_registry.pool_floor)
    try:
        out["code_registry_duckdb_types"] = [
            [r[0], r[1]] for r in con.execute("DESCRIBE src.public.code_registry").fetchall()
        ]
    except Exception as e:  # noqa: BLE001
        out["code_registry_duckdb_types"] = f"ERR {type(e).__name__}: {e}"
    # CTAS into a scratch file: constraints dropped? halfvec text parseable?
    scon = duckdb.connect(str(SCRATCH_DB))
    scon.execute("LOAD postgres")
    if "'" in DSN or "\x00" in DSN:
        raise SystemExit("DSN must not contain quotes or NUL bytes")
    scon.execute("ATTACH '" + DSN.replace("'", "''") + "' AS src (TYPE POSTGRES, READ_ONLY)")
    scon.execute(
        """
        CREATE TABLE scratch_chunks AS
        SELECT * FROM src.public.chunks ORDER BY chunk_id LIMIT 5
        """
    )
    scon.execute(
        """
        CREATE TABLE scratch_vectors AS
        SELECT * FROM src.public.chunks_vectors ORDER BY chunk_id LIMIT 5
        """
    )
    n = scon.execute("SELECT count(*) FROM scratch_chunks").fetchone()[0]
    out["scratch_ctas_rows"] = n
    out["scratch_vectors_rows"] = scon.execute(
        "SELECT count(*) FROM scratch_vectors"
    ).fetchone()[0]
    # Constraints: DuckDB CTAS drops PK/FK/NOT NULL — duckdb_constraints is empty.
    out["scratch_constraints"] = scon.execute(
        "SELECT * FROM duckdb_constraints() WHERE table_name IN ('scratch_chunks','scratch_vectors')"
    ).fetchall()
    cols = [r[0] for r in scon.execute("DESCRIBE scratch_vectors").fetchall()]
    types = {r[0]: r[1] for r in scon.execute("DESCRIBE scratch_vectors").fetchall()}
    out["scratch_vectors_cols"] = {c: types[c] for c in cols}
    vec_col = next((c for c in cols if "vec" in c.lower() or "half" in c.lower()), None)
    if vec_col:
        vec_col = _ident(vec_col)
        typ = types[vec_col]
        row = scon.execute(
            "SELECT " + vec_col + " FROM scratch_vectors"
            " WHERE " + vec_col + " IS NOT NULL LIMIT 1"
        ).fetchone()
        sample = row[0] if row else None
        out["halfvec_sample"] = {
            "duckdb_type": typ,
            "python_type": type(sample).__name__,
            "value_prefix": str(sample)[:90] if sample is not None else None,
            "parseable": None,
        }
        if sample is not None:
            try:
                txt = sample if isinstance(sample, str) else str(sample)
                body = txt.strip().strip("[]")
                vals = [float(x) for x in body.split(",") if x.strip()]
                out["halfvec_sample"]["parseable"] = f"yes, {len(vals)} dims"
            except Exception as e:  # noqa: BLE001
                out["halfvec_sample"]["parseable"] = f"NO: {e}"
    con.close()
    scon.close()
    return out


def main() -> None:
    report = {"pg_side": asyncio.run(pg_side()), "duck_side": duck_side()}
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
