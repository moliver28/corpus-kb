"""Spike 5: FLOAT[2560] typing — inserts (list/numpy), param in ORDER BY, varchar cast parse."""
import duckdb
import time
import numpy as np

con = duckdb.connect(":memory:")
DIM = 2560

print("=== CREATE TABLE with FLOAT[2560] ===")
con.execute("CREATE TABLE t(id INTEGER, embedding FLOAT[2560])")
print("created OK; table types:", con.execute("SELECT typeof(embedding) FROM t LIMIT 0").fetchall() or con.execute("DESCRIBE t").fetchall())

rng = np.random.default_rng(7)
mat = rng.standard_normal((5, DIM)).astype(np.float32)

print("\n=== insert python list (executemany) ===")
t0 = time.perf_counter()
con.executemany("INSERT INTO t VALUES (?, ?)", [(i, mat[i].tolist()) for i in range(5)])
print(f"OK 5 rows in {time.perf_counter()-t0:.3f}s")

print("=== insert numpy float32 array directly (single execute, no .tolist()) ===")
try:
    con.execute("INSERT INTO t VALUES (?, ?)", [99, mat[0]])
    print("OK numpy direct insert")
except Exception as e:
    print("FAIL:", type(e).__name__, "|", str(e)[:200])

print("\n=== query: ORDER BY array_cosine_distance(embedding, $1::FLOAT[2560]) LIMIT 3, list param ===")
qvec = rng.standard_normal(DIM).astype(np.float32)
try:
    rows = con.execute("SELECT id, array_cosine_distance(embedding, $1::FLOAT[2560]) AS d FROM t ORDER BY d LIMIT 3",
                       [qvec.tolist()]).fetchall()
    print("OK:", [(r[0], round(r[1], 6)) for r in rows])
except Exception as e:
    print("FAIL:", type(e).__name__, "|", str(e)[:300])

print("=== same with ? form and no explicit cast ===")
try:
    rows = con.execute("SELECT id, array_cosine_distance(embedding, ?) AS d FROM t ORDER BY d LIMIT 3",
                       [qvec.tolist()]).fetchall()
    print("OK:", [(r[0], round(r[1], 6)) for r in rows])
except Exception as e:
    print("FAIL:", type(e).__name__, "|", str(e)[:300])

print("\n=== varchar '[...]' CAST to FLOAT[2560] (feeds halfvec-text re-parse) ===")
vec_str = '[' + ','.join(f"{x:.6f}" for x in qvec[:10]) + ',...]'  # malformed tail on purpose? no, build full
vec_str = '[' + ','.join(f"{x:.6f}" for x in qvec) + ']'
print("str len:", len(vec_str))
for label, s in [("no-space '[a,b]'", vec_str),
                 ("with spaces", '[' + ', '.join(f"{x:.6f}" for x in qvec[:5]) + ']'),
                 ("pg halfvec text '[0.1,0.2]'", '[0.1,0.2]')]:
    try:
        dim = 2560 if len(s) > 100 else 2
        if dim == 2560:
            r = con.execute("SELECT len(CAST($1 AS FLOAT[2560]))", [s]).fetchone()
        else:
            r = con.execute("SELECT len(CAST($1 AS FLOAT[2])), CAST($1 AS FLOAT[2])", [s]).fetchone()
        print(f"  {label}: OK {r}")
    except Exception as e:
        print(f"  {label}: FAIL {type(e).__name__}: {str(e)[:150]}")

print("=== cheaper parse: direct CAST vs list_transform(string_split)? ===")
t0 = time.perf_counter()
con.execute("CREATE TABLE tv AS SELECT CAST($1 AS FLOAT[2560]) AS embedding", [vec_str])
print(f"CTAS direct CAST: {time.perf_counter()-t0:.4f}s")
try:
    r = con.execute("SELECT list_transform(string_split(trim(trim($1,'['),']'), ','), x -> x::FLOAT) AS l", [vec_str]).fetchall()
    print(f"string_split path len: {len(r[0][0])} (returns LIST[FLOAT], untyped)")
except Exception as e:
    print("string_split path FAIL:", str(e)[:150])

print("\n=== insert via CAST from varchar (migration path) ===")
try:
    con.execute("INSERT INTO t SELECT 100, CAST($1 AS FLOAT[2560])", [vec_str])
    print("INSERT SELECT CAST OK; len:", con.execute("SELECT len(embedding) FROM t WHERE id=100").fetchone())
except Exception as e:
    print("FAIL:", type(e).__name__, "|", str(e)[:200])

print("\n=== dimensional mismatch behavior ===")
try:
    con.execute("SELECT array_cosine_distance(embedding, $1::FLOAT[2559]) FROM t LIMIT 1", [qvec[:2559].tolist()])
except Exception as e:
    print("mismatch 2559:", type(e).__name__, "|", str(e)[:150])
try:
    con.execute("SELECT array_cosine_distance(embedding, $1::FLOAT[2560]) FROM t LIMIT 1", [(qvec[:2560]*1).tolist()[:1280] + [0.0]*1280])
    print("zero-padded wrong-direction: bound OK (semantics)")
except Exception as e:
    print("padded FAIL:", type(e).__name__, "|", str(e)[:150])

print("\n=== numpy → param binding without tolist? ===")
try:
    rows = con.execute("SELECT id FROM t ORDER BY array_cosine_distance(embedding, $1::FLOAT[2560]) LIMIT 1", [qvec]).fetchall()
    print("OK numpy array direct:", rows)
except Exception as e:
    print("FAIL:", type(e).__name__, "|", str(e)[:200])
