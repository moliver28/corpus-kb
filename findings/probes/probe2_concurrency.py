"""S3 spike 2: in-process concurrency semantics of duckdb 1.4.5."""
from __future__ import annotations

import threading
from pathlib import Path

import duckdb

BASE = Path(__file__).resolve().parent
DB = str(BASE / "db2" / "shared.duckdb")
Path(DB).parent.mkdir(exist_ok=True)


def identity() -> None:
    c1 = duckdb.connect(DB)
    c1.execute("CREATE OR REPLACE TABLE t (i INTEGER)")
    c1.execute("INSERT INTO t VALUES (7)")
    c2 = duckdb.connect(DB)
    print("IDENTITY is-same-obj:", c1 is c2)
    print("IDENTITY same-file-visible-without-close:", c2.execute("SELECT * FROM t").fetchall())
    print("IDENTITY writes-via-c1-see-by-c2:",
          (c1.execute("INSERT INTO t VALUES (8)"), c2.execute("SELECT count(*) FROM t").fetchone()))
    # shared transaction? BEGIN on c1, can c2 see uncommitted?
    c1.execute("BEGIN")
    c1.execute("INSERT INTO t VALUES (9)")
    print("IDENTITY c2-sees-uncommitted:",
          c2.execute("SELECT count(*) FROM t").fetchone(), "(if 3 -> shared tx; if 2 -> separate)")
    c1.execute("ROLLBACK")
    c1.close()
    c2.close()


def threads_cursor() -> None:
    con = duckdb.connect(DB)
    con.execute("CREATE OR REPLACE TABLE bench (k INTEGER, v VARCHAR)")
    errors: list[str] = []
    reads_total = [0]
    stop = threading.Event()

    def reader(tid: int) -> None:
        cur = con.cursor()
        n = 0
        try:
            while not stop.is_set():
                rows = cur.execute("SELECT count(*) FROM bench").fetchall()
                n += rows[0][0]
        except Exception as e:  # noqa: BLE001
            errors.append(f"reader{tid}: {type(e).__name__}: {e}")
        finally:
            cur.close()
            reads_total[0] += 1
            del n

    def writer() -> None:
        cur = con.cursor()
        try:
            for i in range(2000):
                cur.execute("INSERT INTO bench VALUES (?, ?)", [i, f"v{i}"])
            cur.execute("BEGIN")
            for i in range(2000, 3000):
                cur.execute("INSERT INTO bench VALUES (?, ?)", [i, f"v{i}"])
            cur.execute("COMMIT")
        except Exception as e:  # noqa: BLE001
            errors.append(f"writer: {type(e).__name__}: {e}")
        finally:
            cur.close()

    rthreads = [threading.Thread(target=reader, args=(i,)) for i in range(4)]
    for t in rthreads:
        t.start()
    w = threading.Thread(target=writer)
    w.start()
    w.join()
    stop.set()
    for t in rthreads:
        t.join()
    final = con.execute("SELECT count(*) FROM bench").fetchone()[0]
    print("THREADS-CURSOR errors:", errors if errors else "none")
    print("THREADS-CURSOR final-count:", final, "(expect 3000)")
    print("THREADS-CURSOR verdict:",
          "OK" if not errors and final == 3000 else "BROKEN")
    con.close()


def threads_single_conn() -> None:
    """Control: 4 readers sharing the ONE connection object (no cursor()) - is that safe?"""
    con = duckdb.connect(DB)
    con.execute("CREATE OR REPLACE TABLE bench2 (k INTEGER)")
    errors: list[str] = []
    stop = threading.Event()

    def hammer() -> None:
        try:
            while not stop.is_set():
                con.execute("SELECT count(*) FROM bench2").fetchall()
        except Exception as e:  # noqa: BLE001
            errors.append(f"{type(e).__name__}: {str(e)[:120]}")

    ts = [threading.Thread(target=hammer) for _ in range(4)]
    for t in ts:
        t.start()
    stop.set()
    for t in ts:
        t.join()
    print("SINGLE-CONN-SHARED errors:", errors[:3] if errors else "none")
    con.close()


if __name__ == "__main__":
    print("== identity ==")
    identity()
    print("== 4 readers via cursor() + 1 writer ==")
    threads_cursor()
    print("== control: shared single connection (no cursor) ==")
    threads_single_conn()
