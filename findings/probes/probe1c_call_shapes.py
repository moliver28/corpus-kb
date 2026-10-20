"""Debug 2: find the working call shape for match_bm25 in 1.4.5."""
import duckdb

con = duckdb.connect(":memory:")
con.execute("CREATE TABLE chunks(chunk_id VARCHAR, text VARCHAR)")
con.execute("INSERT INTO chunks VALUES ('a','the needle in the haystack'), ('b','plain hay')")
con.execute("PRAGMA create_fts_index('main.chunks','chunk_id','text')")

def t(label, sql, params=None):
    try:
        rows = con.execute(sql, params or []).fetchall()
        print(f"{label}: OK {rows}")
    except Exception as e:
        print(f"{label}: FAIL {type(e).__name__} | {str(e)[:250]}")

t("6 positional args", "SELECT chunk_id, score FROM fts_main_chunks.match_bm25(chunk_id, 'needle', NULL, 1.2, 0.75, 0) WHERE score IS NOT NULL")
t("named conjunctive", "SELECT chunk_id, score FROM fts_main_chunks.match_bm25(chunk_id, 'needle', conjunctive := 0) WHERE score IS NOT NULL")
t("3 args fields=NULL", "SELECT chunk_id, score FROM fts_main_chunks.match_bm25(chunk_id, 'needle', NULL) WHERE score IS NOT NULL")
t("literal docname arg", "SELECT * FROM fts_main_chunks.match_bm25('a', 'needle') LIMIT 3")
t("scalar-style call", "SELECT chunk_id, fts_main_chunks.match_bm25(chunk_id, 'needle') AS s FROM chunks")
t("select from macro subquery via table()?", "SELECT * FROM fts_main_chunks.match_bm25(chunk_id, 'needle')")

print("\n=== full macro definition ===")
d = con.execute("SELECT macro_definition FROM duckdb_functions() WHERE function_name='match_bm25'").fetchone()[0]
print(d)
print("\n=== parameter_defaults? ===")
cols = [c[0] for c in con.execute("SELECT * FROM duckdb_functions() LIMIT 0").description]
print("duckdb_functions columns:", cols)
row = con.execute("SELECT * FROM duckdb_functions() WHERE function_name='match_bm25'").fetchone()
for c, v in zip(cols, row):
    print(f"  {c} = {v!r}"[:300])

print("\n=== recreate macro manually as TABLE macro with 2 params and call ===")
try:
    con.execute("CREATE OR REPLACE MACRO fts_main_chunks.match_bm25(docname, query_string, fields = NULL, k = 1.2, b = 0.75, conjunctive = 0) AS TABLE (SELECT docs.name AS docname, 1.0 AS score FROM fts_main_chunks.docs AS docs WHERE docs.name = docname)")
    t("manual table macro 2 args", "SELECT chunk_id, score FROM fts_main_chunks.match_bm25(chunk_id, 'needle') WHERE score IS NOT NULL")
except Exception as e:
    print("manual macro create FAIL:", str(e)[:300])

print("\n=== check duckdb-fts version bundled ===")
try:
    print(con.execute("PRAGMA fts_version?").fetchall())
except Exception as e:
    print("no fts_version pragma:", str(e)[:150])
