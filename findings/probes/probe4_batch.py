"""S3 spike 4: batch-insert microbench at FLOAT[2560], 2000 rows."""
from __future__ import annotations

import time
import tracemalloc
from pathlib import Path

import duckdb

BASE = Path(__file__).resolve().parent
DB = str(BASE / "db4" / "batch.duckdb")
Path(DB).parent.mkdir(exist_ok=True)

D = 2560
N = 2000
CHUNK = 500

con = duckdb.connect(DB)
con.execute(f"CREATE OR REPLACE TABLE vecs (id INTEGER, v FLOAT[{D}])")

import array  # noqa: E402


def make_vec(seed: int) -> list[float]:
    # deterministic pseudo-floats, no numpy dependency question
    return [((seed * 31 + j) % 1000) / 1000.0 for j in range(D)]


rows = [(i, make_vec(i)) for i in range(N)]

# sanity: check con.append availability and signature
print("HAS-APPEND:", hasattr(con, "append"))
try:
    import inspect
    print("APPEND-SIG:", inspect.signature(con.append))
except Exception as e:  # noqa: BLE001
    print("APPEND-SIG-ERR:", e)
try:
    import pandas  # noqa: F401
    print("PANDAS: present", pandas.__version__)
except Exception as e:  # noqa: BLE001
    print("PANDAS: absent", e)


def bench(label: str, fn) -> None:
    con.execute("DELETE FROM vecs")
    con.execute("CHECKPOINT")
    tracemalloc.start()
    t0 = time.perf_counter()
    fn()
    dt = time.perf_counter() - t0
    cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    n = con.execute("SELECT count(*), max(id) FROM vecs").fetchone()
    rows_per_s = N / dt if dt else 0
    print(f"{label}: {dt*1000:.1f} ms | {rows_per_s:.0f} rows/s | py-peak {peak/1e6:.1f} MB | count={n[0]} maxid={n[1]}")


def row_by_row() -> None:
    for i, v in rows:
        con.execute("INSERT INTO vecs VALUES (?, ?)", [i, v])


def executemany() -> None:
    con.executemany("INSERT INTO vecs VALUES (?, ?)", rows)


def try_chunk(rows_per_stmt: int) -> bool:
    # NOTE: a FLOAT[2560] column binds as ONE list param; a row is (?, ?)
    ph = "(?, ?)"
    start = 0
    try:
        while start < N:
            chunk = rows[start:start + rows_per_stmt]
            sql = "INSERT INTO vecs VALUES " + ",".join([ph] * len(chunk))
            flat: list = []
            for i, v in chunk:
                flat.append(i)
                flat.append(v)
            con.execute(sql, flat)
            start += rows_per_stmt
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  chunk={rows_per_stmt} FAILS -> {type(e).__name__}: {str(e)[:120]}")
        return False


def find_max_chunk() -> int:
    print("VALUES-CHUNK BISECT (params/row =", D + 1, "):")
    for size in (CHUNK, 200, 100, 50, 25, 10, 5, 1):
        if try_chunk(size):
            print(f"  chunk={size} OK ({size * (D + 1)} params/stmt)")
            return size
    return 0


def values_chunks() -> None:
    size = find_max_chunk()
    if size == 0:
        print("C values-chunk : SKIPPED (no chunk size worked)")
        return
    ph = "(?, ?)"
    for start in range(0, N, size):
        chunk = rows[start:start + size]
        sql = "INSERT INTO vecs VALUES " + ",".join([ph] * len(chunk))
        flat: list = []
        for i, v in chunk:
            flat.append(i)
            flat.append(v)
        con.execute(sql, flat)


def df_append() -> None:
    try:
        import pandas as pd
    except ImportError:
        print("DF-APPEND: skipped (no pandas)")
        return
    df = pd.DataFrame(rows, columns=["id", "v"])
    con.append("vecs", df)


def df_insert_select() -> None:
    try:
        import pandas as pd
    except ImportError:
        print("DF-INSERT-SELECT: skipped (no pandas)")
        return
    df = pd.DataFrame(rows, columns=["id", "v"])
    con.register("batch_df", df)
    con.execute("INSERT INTO vecs SELECT * FROM batch_df")
    con.unregister("batch_df")


bench("A row-by-row   ", row_by_row)
bench("B executemany  ", executemany)
# C: DELETE before bench already happened, but bisect inserts 2000 rows once as probe;
# bench() deletes again first so timing is clean
bench("C values-chunk ", values_chunks)
bench("D con.append   ", df_append)
bench("E df+insert-sel", df_insert_select)

# vector data volume note
print(f"DATA-VOLUME: {N} x FLOAT[{D}] = {N * D * 4 / 1e6:.1f} MB per full table")
con.close()
