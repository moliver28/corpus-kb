"""S3 spike 7: connect-time config keys on duckdb 1.4.5 + progress bar default."""
from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

import duckdb

BASE = Path(__file__).resolve().parent
DB = str(BASE / "db7" / "cfg.duckdb")
Path(DB).parent.mkdir(exist_ok=True)

CANDIDATES = {
    "default_null_order": "nulls_last",
    "default_order": "desc",
    "threads": 4,
    "memory_limit": "1GB",
    "temp_directory": str(BASE / "db7" / "tmp"),
    "enable_external_access": True,
    "autoload_known_extensions": False,
    "autoinstall_known_extensions": False,
    "extension_directory": str(BASE / "db7" / "ext"),
    "max_memory": "2GB",
    "worker_threads": 4,
    "preserve_insertion_order": False,
}


def probe_accept() -> None:
    for key, val in sorted(CANDIDATES.items()):
        try:
            con = duckdb.connect(DB, config={key: val})
            print(f"ACCEPT {key}={val!r}")
            con.close()
        except Exception as e:  # noqa: BLE001
            print(f"REJECT {key}={val!r} -> {type(e).__name__}: {str(e)[:160]}")


def roundtrip_setting() -> None:
    con = duckdb.connect(DB, config={
        "default_null_order": "nulls_last",
        "threads": 4,
        "memory_limit": "1GB",
    })
    for s in ("default_null_order", "threads", "memory_limit"):
        try:
            print("SETTING", s, "=", con.execute(f"SELECT current_setting('{s}')").fetchone()[0])
        except Exception as e:  # noqa: BLE001
            print("SETTING", s, "ERR:", e)
    # progress bar: not a connect config key -- test SET-after-connect instead
    for sql in ("SET enable_progress_bar = false", "SET enable_progress_bar = true"):
        try:
            con.execute(sql)
            print("SET-OK:", sql, "->",
                  con.execute("SELECT current_setting('enable_progress_bar')").fetchone()[0])
        except Exception as e:  # noqa: BLE001
            print("SET-ERR:", sql, "->", type(e).__name__, str(e)[:160])
    con.close()


NO_TTY_CHILD = r"""
import duckdb, sys
con = duckdb.connect(sys.argv[1])
print("progress_bar_default:", con.execute("SELECT current_setting('enable_progress_bar')").fetchone()[0])
con.close()
"""


def progress_default_no_tty() -> None:
    env = dict(os.environ)
    r = subprocess.run([sys.executable, "-c", NO_TTY_CHILD, DB],
                       capture_output=True, text=True, timeout=60, env=env,
                       stdin=subprocess.DEVNULL)
    out = r.stdout.strip()
    # also probe duckdb.__interactive__ if exposed
    print("NO-TTY:", out if out else f"rc={r.returncode} {r.stderr.strip()[:200]}")
    print("INTERACTIVE-FLAG:", getattr(duckdb, "__interactive__", "not exposed"))


def bad_key() -> None:
    try:
        con = duckdb.connect(DB, config={"definitely_not_a_setting": 1})
        print("BAD-KEY: accepted?!)")
        con.close()
    except Exception as e:  # noqa: BLE001
        print("BAD-KEY-ERRTYPE:", type(e).__module__, type(e).__name__)
        print("BAD-KEY-MSG:", str(e)[:250])


if __name__ == "__main__":
    print("== candidate keys ==")
    probe_accept()
    print("== settings roundtrip ==")
    roundtrip_setting()
    print("== progress bar default with stdout NOT a tty ==")
    progress_default_no_tty()
    print("== unknown key behavior ==")
    bad_key()
