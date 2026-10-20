"""Debug: where does match_bm25 live and what is its definition in 1.4.5?"""
import duckdb

con = duckdb.connect(":memory:")
con.execute("LOAD fts")
print("=== extensions ===")
for r in con.execute("SELECT extension_name, loaded, installed, install_mode FROM duckdb_extensions() WHERE extension_name IN ('fts','vss','core_fns')").fetchall():
    print(r)

con.execute("CREATE TABLE chunks(chunk_id VARCHAR, text VARCHAR)")
con.execute("INSERT INTO chunks VALUES ('a','the needle in the haystack'), ('b','plain hay')")
con.execute("PRAGMA create_fts_index('main.chunks','chunk_id','text')")

print("=== schemas ===")
print([r[0] for r in con.execute("SELECT database_name || '.' || schema_name FROM duckdb_schemas()").fetchall()])

print("=== match_bm25 catalog entries (schema-qualified) ===")
for r in con.execute("""
    SELECT schema_name, function_name, function_type, parameters, parameter_types, macro_definition
    FROM duckdb_functions() WHERE function_name LIKE '%bm25%'
""").fetchall():
    print("schema:", r[0], "| type:", r[2], "| params:", r[3], "| ptypes:", r[4])
    print("def:", (r[5] or "")[:1500])
    print("---")

print("=== tables in fts_main_chunks ===")
print(con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='fts_main_chunks'").fetchall())

print("=== try documented call form: schema-qualified FROM with lateral key ===")
try:
    rows = con.execute("SELECT chunk_id, score FROM fts_main_chunks.match_bm25(chunk_id, 'needle') WHERE score IS NOT NULL").fetchall()
    print("OK:", rows)
except Exception as e:
    print("FAIL:", type(e).__name__, "|", str(e)[:400])

print("=== try with chunks in FROM (cross join form) ===")
try:
    rows = con.execute("SELECT c.chunk_id, m.score FROM chunks c, fts_main_chunks.match_bm25(c.chunk_id, 'needle') m WHERE m.score IS NOT NULL").fetchall()
    print("OK:", rows)
except Exception as e:
    print("FAIL:", type(e).__name__, "|", str(e)[:400])

print("=== try unqualified match_bm25 ===")
try:
    rows = con.execute("SELECT chunk_id, score FROM match_bm25(chunk_id, 'needle') WHERE score IS NOT NULL").fetchall()
    print("OK:", rows)
except Exception as e:
    print("FAIL:", type(e).__name__, "|", str(e)[:400])
