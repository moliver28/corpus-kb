"""S4 spike 2: eventsourcing.sqlite hermetic drill.

Runs against the shared venv. Writes ONLY to a scratch file inside
findings/probes/scratch/. Verifies: save/read round-trip, actual table
names + columns, journal_mode, subscribe() gap, two-writer locking on
one file.
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
import threading
import time
from pathlib import Path


def _ident(name: str) -> str:
    """Pass plain SQL identifiers; refuse everything else."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""):
        raise SystemExit(f"unsafe SQL identifier: {name!r}")
    return name


SCRATCH = Path(__file__).resolve().parent / "scratch"
SCRATCH.mkdir(exist_ok=True)
DB = SCRATCH / "es_drill.sqlite"
if DB.exists():
    DB.unlink()

os.environ["SQLITE_DBNAME"] = str(DB)
# Minimal env: sqlite module + create tables + no snapshotting.
ENV = {
    "PERSISTENCE_MODULE": "eventsourcing.sqlite",
    "CREATE_TABLE": "y",
    "SQLITE_LOCK_TIMEOUT": "5",  # seconds; default per lib is 5 (SQLITE3_DEFAULT_LOCK_TIMEOUT)
}

from eventsourcing.application import Application  # noqa: E402
from eventsourcing.domain import Aggregate, event  # noqa: E402
from eventsourcing.sqlite import SQLiteApplicationRecorder  # noqa: E402


class Colour(Aggregate):
    @event("Started")
    def __init__(self, name: str) -> None:
        self.name = name

    @event("Renamed")
    def rename(self, name: str) -> None:
        self.name = name


class MinimalApp(Application):
    env = {**Application.env, **ENV}
    snapshotting_intervals = None


def main() -> None:
    report: dict[str, object] = {}

    # --- 1. Save + read round-trip via two independent Application instances.
    app1 = MinimalApp()
    agg = Colour(name="red")
    app1.save(agg)
    agg.rename("blue")
    app1.save(agg)

    app2 = MinimalApp()  # separate instance, same file
    repo_agg = app2.repository.get(agg.id)
    report["roundtrip_name"] = repo_agg.name
    report["roundtrip_version"] = repo_agg.version

    rec = app1.recorder
    report["recorder_class"] = type(rec).__name__
    report["events_table_name"] = getattr(rec, "events_table_name", None)
    nid = rec.max_notification_id()
    report["max_notification_id"] = nid
    notifs = rec.select_notifications(start=0, limit=10, inclusive_of_start=False)
    report["select_notifications_count"] = len(notifs)
    report["first_notification"] = (
        {
            "id": notifs[0].id,
            "topic": notifs[0].topic,
            "originator_id": str(notifs[0].originator_id),
        }
        if notifs
        else None
    )

    # --- 2. subscribe() gap.
    try:
        rec.subscribe(gt=0)
        report["subscribe"] = "NO ERROR (unexpected)"
    except NotImplementedError as e:
        report["subscribe"] = f"NotImplementedError: {e}"

    # --- 3. Actual tables + columns + journal mode via raw sqlite3.
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    tables = [
        r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    ]
    report["tables"] = tables
    schema = {}
    for t in tables:
        statements = {"pragma": "PRAGMA table_info(" + _ident(t) + ")"}
        rows = con.execute(statements["pragma"])
        cols = [(r["name"], r["type"], r["pk"]) for r in rows]
        schema[t] = cols
    report["schema"] = schema
    report["journal_mode"] = con.execute("PRAGMA journal_mode").fetchone()[0]
    con.close()

    # --- 4. Two writers on the same file concurrently.
    errs: list[str] = []
    writes: list[str] = []

    def writer(tag: str) -> None:
        try:
            a = MinimalApp()
            for i in range(25):
                x = Colour(name=f"{tag}-{i}")
                a.save(x)
                writes.append(f"{tag}:{i}")
                time.sleep(0.001)
        except Exception as e:  # noqa: BLE001
            errs.append(f"{tag}: {type(e).__name__}: {e}")

    t1 = threading.Thread(target=writer, args=("A",))
    t2 = threading.Thread(target=writer, args=("B",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    report["concurrent_errors"] = errs
    report["concurrent_writes_ok"] = len(writes)

    # --- 5. Environment knobs of interest.
    from eventsourcing.sqlite import SQLITE3_DEFAULT_LOCK_TIMEOUT

    report["SQLITE3_DEFAULT_LOCK_TIMEOUT"] = SQLITE3_DEFAULT_LOCK_TIMEOUT

    import json

    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
