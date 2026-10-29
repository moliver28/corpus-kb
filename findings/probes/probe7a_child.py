"""Spike 7 child A: create HNSW index (no flag -> with flag), insert rows, then os._exit(1) without close."""
import duckdb
import os
import sys

DB = sys.argv[1]
MODE = sys.argv[2]  # "noflag" or "flag"

if os.path.exists(DB):
    os.remove(DB)
if os.path.exists(DB + ".wal"):
    os.remove(DB + ".wal")

con = duckdb.connect(DB)
con.execute("LOAD vss")
con.execute("CREATE TABLE docs(id INTEGER, embedding FLOAT[8])")
import random
rng = random.SystemRandom()
rows = [(i, [rng.random() for _ in range(8)]) for i in range(200)]
con.executemany("INSERT INTO docs VALUES (?, ?)", rows)

if MODE == "noflag":
    print("CHILD-A: attempting CREATE INDEX without persistence flag", flush=True)
    try:
        con.execute("CREATE INDEX idx_hnsw ON docs USING HNSW (embedding) WITH (metric='cosine')")
        print("CHILD-A: unexpectedly SUCCEEDED without flag", flush=True)
    except Exception as e:
        print(f"CHILD-A: FAILED as expected: {type(e).__name__}: {str(e)[:400]}", flush=True)
    con.close()
    os._exit(0)

# MODE == "flag"
con.execute("SET hnsw_enable_experimental_persistence = true")
try:
    con.execute("CREATE INDEX idx_hnsw ON docs USING HNSW (embedding) WITH (metric='cosine')")
    print("CHILD-A: CREATE INDEX with flag OK", flush=True)
except Exception as e:
    print(f"CHILD-A: FAILED even with flag: {type(e).__name__}: {str(e)[:400]}", flush=True)
    con.close()
    os._exit(1)

# hard exit: no close, WAL + index metadata left for replay
os._exit(1)
