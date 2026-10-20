"""S3 spike 3: transaction-conflict capture on duckdb 1.4.5."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import duckdb

BASE = Path(__file__).resolve().parent
DB = str(BASE / "db3" / "conflict.duckdb")
Path(DB).parent.mkdir(exist_ok=True)


def capture(label: str, fn) -> None:
    try:
        fn()
        print(f"{label}: OK (no error)")
    except Exception as e:  # noqa: BLE001
        print(f"{label}-ERRTYPE: {type(e).__module__}.{type(e).__name__}")
        print(f"{label}-MRO: {[c.__name__ for c in type(e).__mro__]}")
        print(f"{label}-MSG: {e}")


def setup() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(DB)
    con.execute("CREATE OR REPLACE TABLE acct (id INTEGER PRIMARY KEY, bal INTEGER)")
    con.execute("INSERT INTO acct VALUES (1, 100)")
    return con


def same_row_update() -> None:
    con = setup()
    c1, c2 = con.cursor(), con.cursor()
    barrier = threading.Barrier(2)
    errs: list[BaseException] = []

    def upd(cur, delta: int) -> None:
        try:
            cur.execute("BEGIN")
            cur.execute("UPDATE acct SET bal = bal + ? WHERE id = 1", [delta])
            time.sleep(0.15)  # force overlap
            barrier.wait(timeout=10)
            cur.execute("COMMIT")
        except BaseException as e:  # noqa: BLE001
            errs.append(e)
            try:
                cur.execute("ROLLBACK")
            except Exception:  # noqa: BLE001
                pass

    t1 = threading.Thread(target=upd, args=(c1, +10))
    t2 = threading.Thread(target=upd, args=(c2, -10))
    t1.start(); t2.start(); t1.join(); t2.join()
    for e in errs:
        print("SAME-ROW-ERRTYPE:", type(e).__module__, type(e).__name__)
        print("SAME-ROW-MRO:", [c.__name__ for c in type(e).__mro__])
        print("SAME-ROW-MSG:", str(e))
    print("SAME-ROW-BAL:", con.execute("SELECT bal FROM acct WHERE id=1").fetchone(), "errors:", len(errs))
    con.close()


def parallel_append() -> None:
    con = setup()
    con.execute("CREATE OR REPLACE TABLE events (id INTEGER, note VARCHAR)")
    c1, c2 = con.cursor(), con.cursor()
    errs: list[BaseException] = []

    def app(cur, base: int) -> None:
        try:
            cur.execute("BEGIN")
            for i in range(500):
                cur.execute("INSERT INTO events VALUES (?, ?)", [base + i, "x"])
            time.sleep(0.1)
            cur.execute("COMMIT")
        except BaseException as e:  # noqa: BLE001
            errs.append(e)
            try:
                cur.execute("ROLLBACK")
            except Exception:  # noqa: BLE001
                pass

    t1 = threading.Thread(target=app, args=(c1, 0))
    t2 = threading.Thread(target=app, args=(c2, 100000))
    t1.start(); t2.start(); t1.join(); t2.join()
    n = con.execute("SELECT count(*) FROM events").fetchone()[0]
    print("APPEND-ERRORS:", [f"{type(e).__name__}: {e}" for e in errs] if errs else "none")
    print("APPEND-COUNT:", n, "(expect 1000)")
    con.close()


def exception_types() -> None:
    print("DUCKDB-EXC-TREE:")
    for name in ("Error", "IOException", "InvalidInputError", "ConstraintException",
                 "TransactionException", "TransactionConflict", "ConversionException",
                 "ConnectionException", "FatalException", "InternalException"):
        cls = getattr(duckdb, name, None)
        print("  ", name, "->", cls if cls is None else [c.__name__ for c in cls.__mro__])


if __name__ == "__main__":
    exception_types()
    print("== same-row update conflict ==")
    same_row_update()
    print("== parallel append ==")
    parallel_append()
