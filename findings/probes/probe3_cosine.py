"""Spike 3: array_cosine_distance / array_cosine_similarity / array_norm semantics on 1.4.5."""
import duckdb

con = duckdb.connect(":memory:")
con.execute("LOAD vss")  # may be needed; also try core_fns

def show(label, sql, params=None):
    try:
        r = con.execute(sql, params or []).fetchall()
        print(f"{label}: OK -> {r}")
        return r
    except Exception as e:
        print(f"{label}: FAIL {type(e).__name__}: {str(e)[:250]}")
        return None

print("=== function existence ===")
for fn in ("array_cosine_distance", "array_cosine_similarity", "array_norm", "array_distance", "array_inner_product"):
    r = con.execute("SELECT function_name, function_type FROM duckdb_functions() WHERE function_name = ?", [fn]).fetchall()
    print(f"{fn}: {'EXISTS' if r else 'MISSING'} {r}")

print("\n=== exact zero-vector semantics (FLOAT[4]) ===")
show("dist(zero, normal)", "SELECT array_cosine_distance([0.0,0.0,0.0,0.0]::FLOAT[4], [1.0,2.0,3.0,4.0]::FLOAT[4])")
show("sim(zero, normal)", "SELECT array_cosine_similarity([0.0,0.0,0.0,0.0]::FLOAT[4], [1.0,2.0,3.0,4.0]::FLOAT[4])")
show("dist(normal, normal)", "SELECT array_cosine_distance([1.0,2.0,3.0,4.0]::FLOAT[4], [1.0,2.0,3.0,4.0]::FLOAT[4])")
show("sim(normal, normal)", "SELECT array_cosine_similarity([1.0,2.0,3.0,4.0]::FLOAT[4], [1.0,2.0,3.0,4.0]::FLOAT[4])")
show("sim(x, -x) exact -1?", "SELECT array_cosine_similarity([1.0,0.0]::FLOAT[2], [-1.0,0.0]::FLOAT[2])")
show("dist(x, -x) exact 2?", "SELECT array_cosine_distance([1.0,0.0]::FLOAT[2], [-1.0,0.0]::FLOAT[2])")
show("sim(orthogonal) = 0?", "SELECT array_cosine_similarity([1.0,0.0]::FLOAT[2], [0.0,1.0]::FLOAT[2])")
show("dist(orthogonal) = 1?", "SELECT array_cosine_distance([1.0,0.0]::FLOAT[2], [0.0,1.0]::FLOAT[2])")
show("sim(3,4) precision f64", "SELECT array_cosine_similarity([1.0,2.0,3.0]::DOUBLE[3], [3.0,2.0,1.0]::DOUBLE[3])")

print("\n=== zero vs zero ===")
show("dist(zero, zero)", "SELECT array_cosine_distance([0.0,0.0]::FLOAT[2], [0.0,0.0]::FLOAT[2])")
show("sim(zero, zero)", "SELECT array_cosine_similarity([0.0,0.0]::FLOAT[2], [0.0,0.0]::FLOAT[2])")

print("\n=== NULL handling ===")
show("NULL elements in vec", "SELECT array_cosine_distance([NULL,1.0]::FLOAT[2], [1.0,1.0]::FLOAT[2])")
show("NULL literal arg", "SELECT array_cosine_distance(NULL::FLOAT[2], [1.0,1.0]::FLOAT[2])")
show("sim NULL elements", "SELECT array_cosine_similarity([NULL,1.0]::FLOAT[2], [1.0,1.0]::FLOAT[2])")

print("\n=== array_norm (degraded-mode guard: norm>0 check) ===")
show("norm(zero)", "SELECT array_norm([0.0,0.0,0.0]::FLOAT[3])")
show("norm(normal)", "SELECT array_norm([1.0,2.0,2.0]::FLOAT[3])")
show("norm(NULL elements)", "SELECT array_norm([NULL,1.0]::FLOAT[2])")
show("norm zero-vector f64", "SELECT array_norm([0.0,0.0]::DOUBLE[2])")

print("\n=== distance return type ===")
show("typeof dist", "SELECT typeof(array_cosine_distance([0.0,0.0]::FLOAT[2], [1.0,1.0]::FLOAT[2]))")
show("typeof sim", "SELECT typeof(array_cosine_similarity([0.0,0.0]::FLOAT[2], [1.0,1.0]::FLOAT[2]))")
show("typeof norm", "SELECT typeof(array_norm([0.0,0.0]::FLOAT[2]))")

print("\n=== works without LOAD vss? (fresh conn, no load) ===")
con2 = duckdb.connect(":memory:")
try:
    print("no-load dist:", con2.execute("SELECT array_cosine_distance([0.0,0.0]::FLOAT[2], [1.0,1.0]::FLOAT[2])").fetchall())
except Exception as e:
    print("no-load FAIL:", type(e).__name__, "|", str(e)[:200])
