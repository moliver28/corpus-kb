"""S3 spike 6: extension offline vendoring (fts, postgres) on duckdb 1.4.5 Windows."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
VENDORED = BASE / "ext_vendored"
USER_DUCKDB = Path.home() / ".duckdb" / "extensions"


def vendor() -> None:
    if VENDORED.exists():
        shutil.rmtree(VENDORED)
    print("SRC-REPO:", USER_DUCKDB, "exists:", USER_DUCKDB.exists())
    if USER_DUCKDB.exists():
        shutil.copytree(USER_DUCKDB, VENDORED)
    listing = []
    for p in sorted(VENDORED.rglob("*")):
        if p.is_file():
            listing.append({"rel": str(p.relative_to(VENDORED)), "kb": round(p.stat().st_size / 1024)})
    print("VENDORED-TREE:", json.dumps(listing, indent=1))


CHILD = r"""
import duckdb, sys
scratch = sys.argv[1]
con = duckdb.connect(":memory:", config={
    "extension_directory": scratch,
    "autoinstall_known_extensions": False,
    "autoload_known_extensions": False,
})
LOAD_SQL = {"fts": "LOAD fts", "postgres": "LOAD postgres"}
for ext in ("fts", "postgres"):
    try:
        con.execute(LOAD_SQL[ext])
        print(f"LOAD-{ext}: OK")
    except Exception as e:
        print(f"LOAD-{ext}-ERRTYPE: {type(e).__module__}.{type(e).__name__}")
        print(f"LOAD-{ext}-MSG: {e}")
"""


def fresh_process_load() -> None:
    r = subprocess.run([sys.executable, "-c", CHILD, str(VENDORED)],
                       capture_output=True, text=True, timeout=120)
    print(r.stdout.strip())
    if r.returncode:
        print("CHILD-RC:", r.returncode, r.stderr.strip()[:400])


PIN_CHILD = r"""
import duckdb, sys
con = duckdb.connect(":memory:")
for sql in (
    "INSTALL fts VERSION 'v1.4.5'",
    "INSTALL fts",
    "FORCE INSTALL postgres",
):
    try:
        con.execute(sql)
        print(f"OK  : {sql}")
    except Exception as e:
        print(f"ERR : {sql} -> {type(e).__name__}: {str(e)[:200]}")
"""


def pin_syntax() -> None:
    r = subprocess.run([sys.executable, "-c", PIN_CHILD],
                       capture_output=True, text=True, timeout=300)
    print(r.stdout.strip())
    if r.returncode:
        print("PIN-RC:", r.returncode, r.stderr.strip()[:300])


if __name__ == "__main__":
    print("== vendor ==")
    vendor()
    print("== fresh process, offline flags, LOAD from vendored dir ==")
    fresh_process_load()
    print("== INSTALL VERSION pinning syntax ==")
    pin_syntax()
