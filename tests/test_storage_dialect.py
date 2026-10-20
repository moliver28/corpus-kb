"""Tests for storage.dialect SQL shims (program doc section 3.2).

Postgres outputs are string-equality pinned against literals pasted from
today's call sites, so the dialect and the source cannot drift apart.
Multi-line literals compare whitespace-normalized; SQL whitespace carries
no meaning. DuckDB outputs execute against a real in-memory duckdb
connection (hermetic; the fts leg skips when the extension is unavailable).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import TYPE_CHECKING

import pytest

from corpus_kb.errors import ConfigError
from corpus_kb.storage.dialect import (
    DUCKDB,
    POSTGRES,
    Dialect,
    dialect_for,
    sanitize_fts_query,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

pytest.importorskip("duckdb")

import duckdb  # guarded by importorskip above

# Small-dims dialect for the DuckDB execution tests (2560 is the shipped
# production regime; 4 keeps fixture vectors readable).
DUCKDB_SMALL = Dialect(backend="duckdb", vector_dims=4)


def _norm(sql: str) -> str:
    """Collapse whitespace so pasted multi-line literals compare token-wise."""
    return " ".join(sql.split())


def _squash(sql: str) -> str:
    """Remove all whitespace; for multi-line literals with no spaced literals."""
    return "".join(sql.split())


# ---------------------------------------------------------------------------
# Pasted Postgres literals (they must not drift from today's source).
# ---------------------------------------------------------------------------

# handlers/query_handler.py:313-315
HANDLER_FTS_VECTOR = (
    "to_tsvector('english', coalesce(chunks.context_blurb,'') || ' ' || chunks.text)"
)
# handlers/query_handler.py:347 (score select expression, without " AS score")
HANDLER_FTS_RANK = (
    "ts_rank(to_tsvector('english', coalesce(chunks.context_blurb,'') || ' ' ||"
    " chunks.text), plainto_tsquery('english', $1))"
)
# handlers/query_handler.py:351 (WHERE predicate, without the leading AND)
HANDLER_FTS_MATCH = (
    "to_tsvector('english', coalesce(chunks.context_blurb,'') || ' ' || chunks.text)"
    " @@ plainto_tsquery('english', $1)"
)
# research/retrieval.py:310 (unit dense arm; tenant $1, project $2, vector $3)
RETRIEVAL_DISTANCE = "u.embedding::halfvec(1024) <=> $3::halfvec(1024)"
# research/retrieval.py:361
RETRIEVAL_FTS_RANK = "ts_rank(to_tsvector('english', u.text), plainto_tsquery('english', $3))"
# research/retrieval.py:366
RETRIEVAL_FTS_MATCH = "to_tsvector('english', u.text) @@ plainto_tsquery('english', $3)"
# projections/research/_embed.py:166
EMBED_MATRYOSHKA = "l2_normalize(subvector($3::vector, 1, 256))"
# tools/ingest_common.py:263
ADVISORY_LOCK_SQL = "SELECT pg_try_advisory_lock(hashtextextended($1 || ':' || $2, 0))"
# tools/ingest_common.py:305
ADVISORY_UNLOCK_SQL = "SELECT pg_advisory_unlock(hashtextextended($1 || ':' || $2, 0))"
# research/analytics_sql.py:168-169
PLANNER_SEQSCAN_OFF = "SET LOCAL enable_seqscan = off"
PLANNER_SORT_OFF = "SET LOCAL enable_sort = off"


# ---------------------------------------------------------------------------
# Postgres byte-identity
# ---------------------------------------------------------------------------


def test_pg_vector_distance_matches_arm_literals() -> None:
    """The <=> cast forms of handlers/query_vector_arms.py, byte-identical."""
    # ollama arm, line 286: 1 - (cv.embedding <=> $1::halfvec) AS score
    assert POSTGRES.vector_distance("cv.embedding", "$1::halfvec") == "cv.embedding <=> $1::halfvec"
    # matryoshka arm, line 247: ORDER BY cv.vector_1024 <=> $4::vector
    assert POSTGRES.vector_distance("cv.vector_1024", "$4::vector") == (
        "cv.vector_1024 <=> $4::vector"
    )
    # pgml arm, lines 136-138 (source wraps the subselect across lines)
    pgml_param = "(SELECT * FROM pgml.embed($1, ARRAY[$2]::text[]) LIMIT 1)::halfvec"
    assert _norm(POSTGRES.vector_distance("cv.embedding", pgml_param)) == _norm(
        "cv.embedding <=> (SELECT * FROM pgml.embed($1, ARRAY[$2]::text[]) LIMIT 1)::halfvec"
    )
    # research/retrieval.py:310
    assert POSTGRES.vector_distance("u.embedding::halfvec(1024)", "$3::halfvec(1024)") == (
        RETRIEVAL_DISTANCE
    )


def test_pg_vector_cast_matches_retrieval_literal() -> None:
    """::halfvec(n) matches research/retrieval.py; ::vector(n) is the twin form."""
    assert POSTGRES.vector_cast("$3", 1024) == "$3::halfvec(1024)"
    plain = Dialect(backend="postgres", vector_type="vector")
    assert plain.vector_cast("$3", 1024) == "$3::vector(1024)"


def test_pg_matryoshka_probe_matches_embed_literal() -> None:
    """projections/research/_embed.py:166 subvector/l2_normalize form."""
    assert POSTGRES.matryoshka_probe("$3::vector", 256) == EMBED_MATRYOSHKA


def test_pg_fts_fragments_match_query_handler_literals() -> None:
    """query_handler.py FTS arm, byte-identical for rank and match."""
    assert POSTGRES.fts_match(HANDLER_FTS_VECTOR, "$1") == HANDLER_FTS_MATCH
    assert POSTGRES.fts_rank(HANDLER_FTS_VECTOR, "$1") == HANDLER_FTS_RANK


def test_pg_fts_fragments_match_retrieval_literals() -> None:
    """research/retrieval.py FTS arms (query text is param $3 there)."""
    assert POSTGRES.fts_match("to_tsvector('english', u.text)", "$3") == RETRIEVAL_FTS_MATCH
    assert POSTGRES.fts_rank("to_tsvector('english', u.text)", "$3") == RETRIEVAL_FTS_RANK


def test_pg_jsonb_set_shim_matches_projector_literals() -> None:
    """projections/research/code_projector.py:94 is the exact byte form."""
    got = POSTGRES.jsonb_set_shim("theory", "'{}'::jsonb", "thresholds", "$4")
    assert got == "jsonb_set(COALESCE(theory, '{}'::jsonb), '{thresholds}', $4)"
    # Lines 133-135 and 154-157 span lines; the newline after "(" becomes a
    # space, so compare with all whitespace removed (no spaced literals here).
    assert _squash(POSTGRES.jsonb_set_shim("theory", "'{}'::jsonb", "keywords", "$4::jsonb")) == (
        _squash("jsonb_set( COALESCE(theory, '{}'::jsonb), '{keywords}', $4::jsonb )")
    )
    assert _squash(
        POSTGRES.jsonb_set_shim("theory", "'{}'::jsonb", "exemplar_text_sha256", "$4")
    ) == _squash("jsonb_set( COALESCE(theory, '{}'::jsonb), '{exemplar_text_sha256}', $4 )")


def test_pg_jsonb_set_shim_matches_batching_shape() -> None:
    """research/batching.py:91-96 spells coalesce lowercase; compare shape-wise."""
    got = POSTGRES.jsonb_set_shim(
        "checkpoint",
        "'{}'::jsonb",
        "completed",
        "coalesce(checkpoint->'completed', '[]'::jsonb) || to_jsonb($3::text)",
    )
    expected = (
        "jsonb_set( coalesce(checkpoint, '{}'::jsonb), '{completed}',"
        " coalesce(checkpoint->'completed', '[]'::jsonb) || to_jsonb($3::text) )"
    )
    assert _squash(got).lower() == _squash(expected).lower()


def test_pg_advisory_lock_literals() -> None:
    """tools/ingest_common.py:263,305 pair, byte-identical."""
    assert POSTGRES.advisory_lock("$1", "$2") == ADVISORY_LOCK_SQL
    assert POSTGRES.advisory_unlock("$1", "$2") == ADVISORY_UNLOCK_SQL


def test_pg_hnsw_settings_literals() -> None:
    """research/search_settings.py set_local_statements forms, byte-identical."""
    assert POSTGRES.hnsw_settings("relaxed_order", 40, 20000) == (
        "SET LOCAL hnsw.iterative_scan = 'relaxed_order'",
        "SET LOCAL hnsw.ef_search = 40",
        "SET LOCAL hnsw.max_scan_tuples = 20000",
    )
    # "off" emits nothing, like set_local_statements today.
    assert POSTGRES.hnsw_settings("off", 40, 20000) == ()


def test_pg_planner_flags_literals() -> None:
    """research/analytics_sql.py:168-169 forms, byte-identical, in that order."""
    assert POSTGRES.planner_flags(True, True) == (PLANNER_SEQSCAN_OFF, PLANNER_SORT_OFF)
    assert POSTGRES.planner_flags(True, False) == (PLANNER_SEQSCAN_OFF,)
    assert POSTGRES.planner_flags(False, True) == (PLANNER_SORT_OFF,)
    assert POSTGRES.planner_flags(False, False) == ()


def test_pg_uuid_new() -> None:
    assert POSTGRES.uuid_new() == "gen_random_uuid()"


def test_pg_zero_vector_guard_is_absent_today() -> None:
    """Postgres guards zero vectors client-side (is_all_zero) plus the
    degraded-flag predicate, so the SQL-side guard is vacuous (TRUE)."""
    assert POSTGRES.zero_vector_guard("cv.embedding") == "TRUE"


def test_dialect_object_is_frozen_and_selectable() -> None:
    """The dialect is a frozen injected object; the selector maps backends."""
    assert dialect_for("postgres") is POSTGRES
    assert dialect_for("duckdb") is DUCKDB
    assert POSTGRES is not DUCKDB
    with pytest.raises(FrozenInstanceError):
        POSTGRES.backend = "duckdb"  # type: ignore[misc]
    with pytest.raises(ConfigError, match="postgres or duckdb"):
        dialect_for("sqlite")


# ---------------------------------------------------------------------------
# DuckDB execution (hermetic in-memory connection)
# ---------------------------------------------------------------------------


@pytest.fixture()
def duck_con() -> Iterator[duckdb.DuckDBPyConnection]:
    """In-memory connection with vector and json fixtures."""
    con = duckdb.connect()
    con.execute("CREATE TABLE v(id INTEGER, embedding FLOAT[4])")
    con.execute(
        "INSERT INTO v VALUES "
        "(1, [1.0,0.0,0.0,0.0]), (2, [0.0,1.0,0.0,0.0]), (3, [0.0,0.0,0.0,0.0])"
    )
    con.execute("CREATE TABLE t(id INTEGER, doc JSON)")
    con.execute(
        "INSERT INTO t VALUES "
        """(1, '{"keep":1,"thresholds":{"old":0}}'), (2, NULL), (3, '{"completed":[1,2]}')"""
    )
    try:
        yield con
    finally:
        con.close()


@pytest.fixture()
def fts_con() -> Iterator[duckdb.DuckDBPyConnection]:
    """In-memory connection with a chunks table and a built fts index."""
    con = duckdb.connect()
    try:
        con.execute("INSTALL fts")
        con.execute("LOAD fts")
    except duckdb.Error as exc:
        con.close()
        pytest.skip(f"the duckdb fts extension is not available: {exc}")
    con.execute("CREATE TABLE chunks(chunk_id VARCHAR, text VARCHAR)")
    con.execute(
        "INSERT INTO chunks VALUES "
        "('a', 'the quick brown fox jumps'), "
        "('b', 'a lazy dog sleeps'), "
        "('c', 'needle in a haystack')"
    )
    con.execute(
        "PRAGMA create_fts_index('main.chunks', 'chunk_id', 'text',"
        " stemmer='porter', stopwords='english')"
    )
    try:
        yield con
    finally:
        con.close()


def test_duckdb_vector_distance_orders_by_cosine(duck_con: duckdb.DuckDBPyConnection) -> None:
    """Exact-scan cosine arm: param cast present, zero vector ranks last."""
    sql = f"SELECT id FROM v ORDER BY {DUCKDB_SMALL.vector_distance('embedding', '$1')}, id"
    assert DUCKDB_SMALL.vector_distance("embedding", "$1") == (
        "array_cosine_distance(embedding, $1::FLOAT[4])"
    )
    rows = duck_con.execute(sql, [[1.0, 0.0, 0.0, 0.0]]).fetchall()
    assert [r[0] for r in rows] == [1, 2, 3]


def test_duckdb_vector_cast_executes(duck_con: duckdb.DuckDBPyConnection) -> None:
    """The FLOAT[k] param cast binds a plain Python list to a fixed array."""
    sql = f"SELECT typeof({DUCKDB_SMALL.vector_cast('$1', 4)})"
    assert DUCKDB_SMALL.vector_cast("$1", 4) == "$1::FLOAT[4]"
    assert duck_con.execute(sql, [[0.5, 0.5, 0.0, 0.0]]).fetchone() == ("FLOAT[4]",)


def test_duckdb_matryoshka_probe_slices(duck_con: duckdb.DuckDBPyConnection) -> None:
    """The DuckDB probe is the 1-based array slice."""
    assert DUCKDB_SMALL.matryoshka_probe("embedding", 2) == "embedding[1:2]"
    sql = f"SELECT {DUCKDB_SMALL.matryoshka_probe('embedding', 2)} FROM v WHERE id = 1"
    assert duck_con.execute(sql).fetchone() == ([1.0, 0.0],)


def test_duckdb_fts_match_scalar_macro_shape(fts_con: duckdb.DuckDBPyConnection) -> None:
    """The 1.4 scalar-macro shape: score in a subselect, NULL scores dropped."""
    assert DUCKDB.fts_match("chunk_id", "$1") == "fts_main_chunks.match_bm25(chunk_id, $1) AS score"
    sql = (
        "SELECT chunk_id, score FROM ("
        f"SELECT *, {DUCKDB.fts_match('chunk_id', '$1')} FROM chunks"
        ") sq WHERE score IS NOT NULL ORDER BY score DESC"
    )
    rows = fts_con.execute(sql, ["needle"]).fetchall()
    assert [r[0] for r in rows] == ["c"]
    assert isinstance(rows[0][1], float)


def test_duckdb_fts_rank_bare_call(fts_con: duckdb.DuckDBPyConnection) -> None:
    """The bare bm25 call scores a known row; no table-function form exists."""
    assert DUCKDB.fts_rank("chunk_id", "$1") == "fts_main_chunks.match_bm25(chunk_id, $1)"
    sql = f"SELECT {DUCKDB.fts_rank('chunk_id', '$1')} FROM chunks WHERE chunk_id = 'c'"
    score = fts_con.execute(sql, ["needle"]).fetchone()
    assert score is not None and score[0] > 0.0


def test_duckdb_jsonb_set_shim_wholesale_replace(duck_con: duckdb.DuckDBPyConnection) -> None:
    """Setting an existing object key removes the old value and keeps peers."""
    shim = DUCKDB.jsonb_set_shim("doc", "'{}'", "thresholds", "$1")
    assert shim == (
        "json_merge_patch(json_merge_patch(COALESCE(doc, '{}'),"
        " json_object('thresholds', NULL)), json_object('thresholds', json($1)))"
    )
    duck_con.execute(f"UPDATE t SET doc = {shim} WHERE id = 1", ['{"t":9}'])
    row = duck_con.execute(
        "SELECT doc->'keep', doc->'thresholds'->>'t', doc->'thresholds'->>'old' FROM t WHERE id = 1"
    ).fetchone()
    assert row == ("1", "9", None)


def test_duckdb_jsonb_set_shim_on_null_and_array(duck_con: duckdb.DuckDBPyConnection) -> None:
    """A NULL target becomes the object; arrays replace wholesale."""
    shim = DUCKDB.jsonb_set_shim("doc", "'{}'", "thresholds", "$1")
    duck_con.execute(f"UPDATE t SET doc = {shim} WHERE id = 2", ['{"t":7}'])
    assert duck_con.execute("SELECT doc->'thresholds'->>'t' FROM t WHERE id = 2").fetchone() == (
        "7",
    )
    array_shim = DUCKDB.jsonb_set_shim("doc", "'{}'", "completed", "$1")
    duck_con.execute(f"UPDATE t SET doc = {array_shim} WHERE id = 3", ["[3]"])
    assert duck_con.execute("SELECT doc->'completed' FROM t WHERE id = 3").fetchone() == ("[3]",)


def test_duckdb_advisory_lock_is_a_noop(duck_con: duckdb.DuckDBPyConnection) -> None:
    """The single-writer file lock already serializes. The fragment still
    consumes the key parameters (unused bound parameters raise on duckdb)."""
    assert DUCKDB.advisory_lock("$1", "$2") == "SELECT length($1 || ':' || $2) > 0"
    assert DUCKDB.advisory_unlock("$1", "$2") == "SELECT length($1 || ':' || $2) > 0"
    assert duck_con.execute(DUCKDB.advisory_lock("$1", "$2"), ["t", "h"]).fetchone() == (True,)
    assert duck_con.execute(DUCKDB.advisory_unlock("$1", "$2"), ["t", "h"]).fetchone() == (True,)


def test_duckdb_setting_shims_emit_nothing() -> None:
    """HNSW and planner settings are Postgres-only; DuckDB emits no SQL."""
    assert DUCKDB.hnsw_settings("relaxed_order", 40, 20000) == ()
    assert DUCKDB.hnsw_settings("off", 40, 20000) == ()
    assert DUCKDB.planner_flags(True, True) == ()


def test_duckdb_uuid_new_executes(duck_con: duckdb.DuckDBPyConnection) -> None:
    assert DUCKDB.uuid_new() == "gen_random_uuid()"
    value = duck_con.execute(f"SELECT {DUCKDB.uuid_new()}").fetchone()
    assert value is not None and len(str(value[0])) == 36


def test_duckdb_zero_vector_guard_filters(duck_con: duckdb.DuckDBPyConnection) -> None:
    """Zero vectors drop out; normal vectors stay (array_norm is absent)."""
    assert DUCKDB_SMALL.zero_vector_guard("embedding") == (
        "sqrt(array_inner_product(embedding, embedding)) > 0"
    )
    sql = f"SELECT id FROM v WHERE {DUCKDB_SMALL.zero_vector_guard('embedding')} ORDER BY id"
    assert [r[0] for r in duck_con.execute(sql).fetchall()] == [1, 2]


# ---------------------------------------------------------------------------
# Query sanitizer (client side; used before the lexical arm on both engines)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('unclosed "quote', "unclosed quote"),
        ("hyphen-word", "hyphen-word"),
        ("知识库", "知识库"),
        ("", ""),
        ("   ", ""),
        ("!!! ??? ...", ""),
        ("the dog", "dog"),
        ("it's", ""),
        ("AND OR NOT", ""),
        ("  spaced   out  ", "spaced"),
        ("THE Needle", "Needle"),
    ],
)
def test_sanitize_fts_query_corpus(raw: str, expected: str) -> None:
    assert sanitize_fts_query(raw) == expected


def test_sanitize_fts_query_output_is_bindable(fts_con: duckdb.DuckDBPyConnection) -> None:
    """A sanitized stopword-laden query still runs and still matches."""
    cleaned = sanitize_fts_query('the "needle"!')
    assert cleaned == "needle"
    sql = (
        "SELECT chunk_id, score FROM ("
        f"SELECT *, {DUCKDB.fts_match('chunk_id', '$1')} FROM chunks"
        ") sq WHERE score IS NOT NULL ORDER BY score DESC"
    )
    assert [r[0] for r in fts_con.execute(sql, [cleaned]).fetchall()] == ["c"]


def test_sanitize_fts_query_empty_means_skip_arm() -> None:
    """No surviving terms means the caller skips the arm, never binds ''."""
    assert sanitize_fts_query("it's") == ""
    assert sanitize_fts_query("!!!") == ""
