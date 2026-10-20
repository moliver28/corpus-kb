"""S3 spike 1: DSN shapes for duckdb:/// URLs on Windows."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlparse

import duckdb

BASE = Path(__file__).resolve().parent
DBDIR = BASE / "db1"
DBDIR.mkdir(exist_ok=True)


def dsn_to_path(dsn: str) -> str:
    """duckdb:///C:/data/corpus.duckdb -> C:/data/corpus.duckdb"""
    parsed = urlparse(dsn)
    if parsed.scheme != "duckdb":
        raise ValueError(f"not a duckdb dsn: {dsn!r}")
    path = unquote(parsed.path)
    # urlparse puts "/C:/..." for windows drive letters (netloc empty)
    if re.match(r"^/[A-Za-z]:/", path):
        path = path[1:]
    return path


def path_to_dsn(path: str) -> str:
    """C:/data/corpus.duckdb -> duckdb:///C:/data/corpus.duckdb (forward slashes)"""
    norm = str(path).replace("\\", "/")
    return f"duckdb:///{norm}"


def main() -> None:
    p1 = DBDIR / "a.duckdb"
    con = duckdb.connect(str(p1))
    con.execute("CREATE TABLE t (i INTEGER)")
    con.execute("INSERT INTO t VALUES (42)")
    con.close()

    # round trip forward-slash form
    dsn = "duckdb:///C:/does/not/matter"  # shape only
    print("PARSE:", dsn, "->", dsn_to_path(dsn))
    rt = path_to_dsn(str(p1))
    print("BUILD:", str(p1), "->", rt)
    print("RT-OK:", dsn_to_path(rt) == str(p1).replace("\\", "/"))

    # backslash form through the same helpers
    bs = "duckdb:///" + str(p1)  # str(Path) on win gives backslashes
    print("PARSE-BS:", bs, "->", dsn_to_path(bs))
    print("RT-BS-OK:", dsn_to_path(bs) == str(p1).replace("\\", "/"))

    # url with spaces (unencoded)
    d1 = DBDIR / "with space.duckdb"
    try:
        c = duckdb.connect(path_to_dsn(str(d1)))
        print("SPACES-DSN-CONNECT: OK", duckdb.connect(path_to_dsn(str(d1))).execute("SELECT 1").fetchone())
        c.close()
    except Exception as e:  # noqa: BLE001
        print("SPACES-DSN-CONNECT: FAIL", type(e).__name__, str(e)[:300])
    try:
        c = duckdb.connect(str(d1))
        print("SPACES-RAW-CONNECT: OK")
        c.close()
    except Exception as e:  # noqa: BLE001
        print("SPACES-RAW-CONNECT: FAIL", type(e).__name__, str(e)[:300])

    # read_only=True on MISSING file
    missing = DBDIR / "missing.duckdb"
    try:
        duckdb.connect(str(missing), read_only=True)
        print("RO-MISSING: no error (unexpected)")
    except Exception as e:  # noqa: BLE001
        print("RO-MISSING-ERRTYPE:", type(e).__module__, type(e).__name__)
        print("RO-MISSING-MSG:", str(e))

    # two connections same path: read-write held, then read_only attempt (fresh process rules apply)
    held = DBDIR / "held.duckdb"
    c1 = duckdb.connect(str(held))
    c1.execute("CREATE TABLE t (i INTEGER)")
    try:
        c2 = duckdb.connect(str(held), read_only=True)
        print("RW-THEN-RO: connected OK (unexpected?)")
        c2.close()
    except Exception as e:  # noqa: BLE001
        print("RW-THEN-RO-ERRTYPE:", type(e).__module__, type(e).__name__)
        print("RW-THEN-RO-MSG:", str(e))
    c1.close()

    # reverse order in a fresh-ish state: read_only first then read-write
    c3 = duckdb.connect(str(held), read_only=True)
    try:
        c4 = duckdb.connect(str(held))
        print("RO-THEN-RW: connected OK (unexpected?)")
        c4.close()
    except Exception as e:  # noqa: BLE001
        print("RO-THEN-RW-ERRTYPE:", type(e).__module__, type(e).__name__)
        print("RO-THEN-RW-MSG:", str(e))
    c3.close()

    # cross-process file lock: keep c1 open here, spawn child to connect rw
    c5 = duckdb.connect(str(held))
    import subprocess
    import sys
    child = subprocess.run(
        [sys.executable, "-c",
         f"import duckdb; duckdb.connect(r'{held}')"],
        capture_output=True, text=True, timeout=60,
    )
    print("CROSS-PROC-RW-RC:", child.returncode)
    print("CROSS-PROC-RW-ERRTYPE:", child.stderr.strip().splitlines()[-1] if child.returncode else "none")
    print("CROSS-PROC-RW-FULL:", child.stderr.strip()[:600] if child.returncode else "none")
    c5.close()


if __name__ == "__main__":
    main()
