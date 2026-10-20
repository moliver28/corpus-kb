"""Spike 1 (final): match_bm25 scalar-macro query shape + prepared-statement params, file-backed DB."""
import duckdb
import os

DB = os.path.join(os.path.dirname(__file__), "spike1.duckdb")
if os.path.exists(DB):
    os.remove(DB)
con = duckdb.connect(DB)
con.execute("LOAD fts")
con.execute("CREATE TABLE chunks(chunk_id VARCHAR, text VARCHAR)")
con.execute("INSERT INTO chunks VALUES ('a','the needle in the haystack'), ('b','plain hay'), ('c','needle needle needle')")
con.execute("PRAGMA create_fts_index('main.chunks','chunk_id','text')")

DOC_SHAPE = ("SELECT chunk_id, score FROM (SELECT *, fts_main_chunks.match_bm25(chunk_id, {q}) AS score "
             "FROM chunks) sq WHERE score IS NOT NULL ORDER BY score DESC")

print("=== documented literal shape ===")
try:
    rows = con.execute(DOC_SHAPE.format(q="'needle'")).fetchall()
    print("OK:", rows)
except Exception as e:
    print("FAIL:", type(e).__name__, "|", str(e)[:300])

print("=== $1 param (con.execute with list) ===")
try:
    rows = con.execute(DOC_SHAPE.format(q="$1"), ["needle"]).fetchall()
    print("OK:", rows)
except Exception as e:
    print("FAIL:", type(e).__name__, "|", str(e)[:300])

print("=== ? param ===")
try:
    rows = con.execute(DOC_SHAPE.format(q="?"), ["needle"]).fetchall()
    print("OK:", rows)
except Exception as e:
    print("FAIL:", type(e).__name__, "|", str(e)[:300])

print("=== $1 reused twice in one statement ===")
try:
    sql = ("SELECT chunk_id, score FROM (SELECT *, fts_main_chunks.match_bm25(chunk_id, $1) AS score, "
           "fts_main_chunks.match_bm25(chunk_id, $1) AS score2 FROM chunks) sq WHERE score IS NOT NULL")
    rows = con.execute(sql, ["needle"]).fetchall()
    print("OK:", rows)
except Exception as e:
    print("FAIL:", type(e).__name__, "|", str(e)[:300])

print("=== named-arg conjunctive + param ===")
try:
    sql = ("SELECT chunk_id, score FROM (SELECT *, fts_main_chunks.match_bm25(chunk_id, $1, conjunctive := 0) AS score "
           "FROM chunks) sq WHERE score IS NOT NULL")
    rows = con.execute(sql, ["needle"]).fetchall()
    print("OK:", rows)
except Exception as e:
    print("FAIL:", type(e).__name__, "|", str(e)[:300])

print("=== reopen file DB WITHOUT LOAD fts first (does the index survive? does query work?) ===")
con.close()
con2 = duckdb.connect(DB)
try:
    rows = con2.execute(DOC_SHAPE.format(q="'needle'")).fetchall()
    print("OK without LOAD:", rows)
except Exception as e:
    print("FAIL without LOAD:", type(e).__name__, "|", str(e)[:300])
print("--- now LOAD fts then retry")
con2.execute("LOAD fts")
try:
    rows = con2.execute(DOC_SHAPE.format(q="'needle'")).fetchall()
    print("OK after LOAD:", rows)
except Exception as e:
    print("FAIL after LOAD:", type(e).__name__, "|", str(e)[:300])

print("=== macro persist check: does match_bm25 exist after reopen? ===")
print(con2.execute("SELECT schema_name, function_type FROM duckdb_functions() WHERE function_name='match_bm25'").fetchall())

print("=== fts index objects survive reopen? ===")
print("tables:", con2.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='fts_main_chunks'").fetchall())
con2.close()
os.remove(DB)
print("cleaned up")
