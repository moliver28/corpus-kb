"""Spike 7 parent: vss HNSW persistence flag, WAL-replay with/without LOAD vss, hnsw_compact_index."""
import duckdb
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = "F:/Documents/ZCode/Corpus-kb/.venv/Scripts/python.exe"
DB = os.path.join(HERE, "spike7.duckdb")
CHILD = os.path.join(HERE, "probe7a_child.py")

def run_child(mode):
    print(f"\n=== CHILD ({mode}) ===")
    r = subprocess.run([PY, CHILD, DB, mode], capture_output=True, text=True)
    print(r.stdout.strip())
    if r.stderr.strip():
        print("stderr:", r.stderr.strip()[:300])
    print(f"child exit code: {r.returncode}")
    return r

# --- part 1: without persistence flag ---
run_child("noflag")

# --- part 2: with flag + hard exit (WAL left behind) ---
run_child("flag")
print("\n=== files after hard exit ===")
for f in (DB, DB + ".wal"):
    print(f, os.path.getsize(f) if os.path.exists(f) else "MISSING")

# --- part 3a: reopen WITH vss loaded first ---
print("\n=== reopen WITH 'LOAD vss' first (correct ordering) ===")
con = duckdb.connect(DB)
con.execute("LOAD vss")
print("db opened ok")
try:
    rows = con.execute("SELECT count(*) FROM docs").fetchall()
    print("count(*) from docs:", rows)
except Exception as e:
    print("count FAIL:", type(e).__name__, "|", str(e)[:300])
try:
    qv = [0.5] * 8
    rows = con.execute("SELECT id FROM docs ORDER BY array_distance(embedding, $1::FLOAT[8]) LIMIT 3", [qv]).fetchall()
    print("plain ORDER BY query:", rows)
except Exception as e:
    print("ORDER BY FAIL:", type(e).__name__, "|", str(e)[:300])
# force index use
try:
    rows = con.execute("SELECT id FROM docs ORDER BY embedding <-> $1::FLOAT[8] LIMIT 3", [qv]).fetchall()
    print("index-force <-> query:", rows)
except Exception as e:
    print("force-index query FAIL:", type(e).__name__, "|", str(e)[:400])
# index catalog state
try:
    print("indexes:", con.execute("SELECT index_name, sql FROM duckdb_indexes() WHERE index_name='idx_hnsw'").fetchall()[:1])
except Exception as e:
    print("index catalog FAIL:", str(e)[:200])
con.close()

# --- part 3b: reopen WITHOUT vss loaded (wrong ordering) ---
print("\n=== reopen WITHOUT LOAD vss (wrong ordering rule check) ===")
con2 = duckdb.connect(DB)
print("db opened ok")
try:
    rows = con2.execute("SELECT count(*) FROM docs").fetchall()
    print("count(*) ok:", rows)
except Exception as e:
    print("count FAIL:", type(e).__name__, "|", str(e)[:300])
try:
    rows = con2.execute("SELECT id FROM docs ORDER BY embedding <-> $1::FLOAT[8] LIMIT 3", [[0.5]*8]).fetchall()
    print("<-> query without vss:", rows)
except Exception as e:
    print("<-> without vss FAIL:", type(e).__name__, "|", str(e)[:300])
try:
    con2.execute("SET hnsw_enable_experimental_persistence = true")
    con2.execute("CREATE INDEX idx2 ON docs USING HNSW (embedding) WITH (metric='cosine')")
    print("new HNSW index without LOAD vss: OK (auto-load?)")
except Exception as e:
    print("new HNSW index without LOAD vss FAIL:", type(e).__name__, "|", str(e)[:250])
con2.close()

# --- part 4: delete half the rows, then hnsw_compact_index ---
print("\n=== hnsw_compact_index after deleting half the rows ===")
con3 = duckdb.connect(DB)
con3.execute("LOAD vss")
con3.execute("SET hnsw_enable_experimental_persistence = true")
before = con3.execute("SELECT count(*) FROM docs").fetchone()
print("rows before delete:", before)
con3.execute("DELETE FROM docs WHERE id % 2 = 0")
print("rows after delete:", con3.execute("SELECT count(*) FROM docs").fetchone())
try:
    con3.execute("PRAGMA hnsw_compact_index('idx_hnsw')")
    print("PRAGMA hnsw_compact_index OK")
except Exception as e:
    print("hnsw_compact_index FAIL:", type(e).__name__, "|", str(e)[:300])
try:
    rows = con3.execute("SELECT id FROM docs ORDER BY embedding <-> $1::FLOAT[8] LIMIT 3", [[0.5]*8]).fetchall()
    print("query after compact:", rows)
except Exception as e:
    print("query after compact FAIL:", str(e)[:250])
con3.close()

for f in (DB, DB + ".wal"):
    if os.path.exists(f):
        os.remove(f)
print("\ncleaned up db files")
