"""S3 spike 1b: does duckdb.connect parse duckdb:/// DSNs itself? + corrected round-trip."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlparse

import duckdb

BASE = Path(__file__).resolve().parent
EXISTING = BASE / "db1" / "a.duckdb"  # created by probe1


def dsn_to_path_v2(dsn: str) -> str:
    """Backslash-safe DSN -> path. Normalize \\ to / BEFORE urlparse."""
    parsed = urlparse(dsn.replace("\\", "/"))
    if parsed.scheme != "duckdb":
        raise ValueError(f"not a duckdb dsn: {dsn!r}")
    if parsed.netloc not in ("", "localhost"):
        raise ValueError(f"unexpected netloc in dsn: {dsn!r}")
    path = unquote(parsed.path)
    if re.match(r"^/[A-Za-z]:/", path):
        path = path[1:]
    return path


def path_to_dsn(path: str) -> str:
    return "duckdb:///" + str(path).replace("\\", "/")


def main() -> None:
    # (a) raw DSN straight into duckdb.connect on an EXISTING db
    try:
        con = duckdb.connect(path_to_dsn(EXISTING))
        r = con.execute("SELECT * FROM t").fetchall()
        print("RAW-DSN-CONNECT-EXISTING: OK rows=", r)
        con.close()
    except Exception as e:  # noqa: BLE001
        print("RAW-DSN-CONNECT-EXISTING-ERRTYPE:", type(e).__module__, type(e).__name__)
        print("RAW-DSN-CONNECT-EXISTING-MSG:", str(e)[:300])

    # check side effect: did it create a junk file named 'duckdb:' or similar next to cwd?
    junk = list(BASE.glob("duckdb*")) + list(Path.cwd().glob("duckdb*"))
    print("RAW-DSN-JUNK-FILES:", [str(j) for j in junk] or "none")

    # (b) corrected round-trip on both slash forms + spaces
    for raw in (
        str(EXISTING),
        str(EXISTING).replace("\\", "/"),
        str(BASE / "db1" / "with space.duckdb"),
    ):
        dsn = path_to_dsn(raw)
        back = dsn_to_path_v2(dsn)
        print("RT:", ("OK " if Path(back) == Path(raw) else "FAIL"), "|", raw[-40:], "=>", back[-40:])
    # idempotence
    once = path_to_dsn(str(EXISTING))
    print("IDEMPOTENT:", dsn_to_path_v2(path_to_dsn(dsn_to_path_v2(once))) == str(EXISTING).replace("\\", "/"))
    # plain path passthrough (no scheme) should raise
    try:
        dsn_to_path_v2(str(EXISTING))
        print("NO-SCHEME: accepted (unexpected)")
    except ValueError as e:
        print("NO-SCHEME: ValueError:", e)


if __name__ == "__main__":
    main()
