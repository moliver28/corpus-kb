"""Spike 6: FTS lifecycle microbench — 5000 rows x ~80 words, VARCHAR uuid keys."""
import duckdb
import time
import uuid
import random
import string
import os

DB = os.path.join(os.path.dirname(__file__), "spike6.duckdb")
if os.path.exists(DB):
    os.remove(DB)
con = duckdb.connect(DB)
con.execute("LOAD fts")

WORDS = [w for w in string.ascii_lowercase]  # will build pseudo words
random.seed(11)
vocab = [''.join(random.choices(string.ascii_lowercase, k=random.randint(3, 10))) for _ in range(4000)]

def sentence(n=80):
    return ' '.join(random.choices(vocab, k=n))

print("=== build 5000-row chunks table ===")
t0 = time.perf_counter()
con.execute("CREATE TABLE chunks(chunk_id VARCHAR, text VARCHAR)")
rows = [(str(uuid.uuid4()), sentence(80)) for _ in range(5000)]
con.executemany("INSERT INTO chunks VALUES (?,?)", rows)
print(f"insert 5000 rows: {time.perf_counter()-t0:.3f}s")

print("\n=== PRAGMA create_fts_index('main.chunks','chunk_id','text') ===")
t0 = time.perf_counter()
con.execute("PRAGMA create_fts_index('main.chunks','chunk_id','text')")
t_create = time.perf_counter() - t0
print(f"create_fts_index: {t_create:.3f}s")

schema = con.execute("SELECT DISTINCT schema_name FROM duckdb_functions() WHERE function_name='match_bm25'").fetchone()
print("match_bm25 schema (expected fts_main_chunks):", schema)

print("\n=== drop + recreate (rebuild budget) ===")
t0 = time.perf_counter()
con.execute("PRAGMA drop_fts_index('main.chunks')")
t_drop = time.perf_counter() - t0
t0 = time.perf_counter()
con.execute("PRAGMA create_fts_index('main.chunks','chunk_id','text')")
t_recreate = time.perf_counter() - t0
print(f"drop_fts_index: {t_drop:.3f}s | recreate: {t_recreate:.3f}s | drop+recreate total: {t_drop+t_recreate:.3f}s")

print("\n=== match_bm25 query timing (param-bound, scalar shape) ===")
SHAPE = ("SELECT chunk_id, score FROM (SELECT *, fts_main_chunks.match_bm25(chunk_id, $1) AS score "
         "FROM chunks) sq WHERE score IS NOT NULL ORDER BY score DESC LIMIT 10")
term = random.choice(vocab)
# warm
r = con.execute(SHAPE, [term]).fetchall()
times = []
for _ in range(5):
    t0 = time.perf_counter()
    rows_q = con.execute(SHAPE, [term]).fetchall()
    times.append(time.perf_counter() - t0)
print(f"single-term query x5: min {min(times)*1000:.1f}ms max {max(times)*1000:.1f}ms; rows={len(rows_q)}")

multi = f"{random.choice(vocab)} {random.choice(vocab)} {random.choice(vocab)}"
times = []
for _ in range(5):
    t0 = time.perf_counter()
    rows_q = con.execute(SHAPE, [multi]).fetchall()
    times.append(time.perf_counter() - t0)
print(f"3-term query x5: min {min(times)*1000:.1f}ms max {max(times)*1000:.1f}ms; rows={len(rows_q)}")

print("\n=== correlated-scalar cost check: count rows evaluated per query ===")
print(con.execute("SELECT count(*) FROM chunks").fetchone(), "rows; docs table:", con.execute("SELECT count(*) FROM fts_main_chunks.docs").fetchone())

print("\n=== incremental problem: insert 100 new rows post-index, are they searchable? ===")
con.execute("INSERT INTO chunks VALUES (?, ?)", [str(uuid.uuid4()), f"uniqueterm123 {sentence(50)}"])
try:
    r = con.execute(SHAPE, ["uniqueterm123"]).fetchall()
    print("post-insert search hits:", len(r), "-> index auto-updates" if r else "-> index STALE, no update (docs warning confirmed)")
except Exception as e:
    print("RAISED:", type(e).__name__, str(e)[:150])

con.close()
print("db kept for inspection:", DB)
