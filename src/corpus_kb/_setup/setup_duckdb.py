"""``corpus-kb setup --backend duckdb``: the no-Docker one-command path.

Storage portability program §9 (switch UX, step 3): provision the
analytics ``.duckdb`` file, apply ``migrations_duckdb`` through the
SHARED migrate runner (``_setup.migrate.run_migrations``), provision the
SQLite event-store sidecar through the ONE path helper
(``storage.duckdb_support.events_sidecar_path``), pre-install the ``fts``
extension, and write the config pair (``database.backend`` + DSN) into
the local ``config.yaml``. The Postgres path in ``install.setup_cmd`` is
untouched; ``--dry-run`` previews every step without touching disk.

One confirmation covers the run (the steps are local files plus one
surgical config edit), unlike the per-phase Postgres confirmations.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

from corpus_kb._setup.config_writeback import config_write_target, write_database_pair
from corpus_kb._setup.migrate import run_migrations
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string, events_sidecar_path

#: The default analytics file, relative to the working directory.
DEFAULT_DUCKDB_FILE = "corpus.duckdb"

#: (extension, required) — installed best-effort before first use.
INSTALL_EXTENSIONS: tuple[tuple[str, bool], ...] = (("fts", True), ("vss", False))


def resolve_duckdb_dsn(config: dict[str, Any], dsn_arg: str) -> str:
    """The analytics DSN: --dsn (path or duckdb:///), else config, else default.

    A bare relative --dsn path is resolved to ABSOLUTE before the DSN is
    built: ``events_sidecar_path`` normalizes through the URL-stripped
    path, and a relative input would put the sidecar at the drive root,
    beside nothing. Absolute inputs (and a config DSN the user wrote)
    pass through unchanged.
    """
    if dsn_arg:
        if "://" in dsn_arg:
            return dsn_arg
        return duckdb_path_to_conn_string(str(Path(dsn_arg).resolve()))
    configured = str((config.get("database") or {}).get("connection_string") or "")
    if configured.startswith("duckdb:"):
        return configured
    return duckdb_path_to_conn_string(str(Path(DEFAULT_DUCKDB_FILE).resolve()))


def duckdb_extra_needed() -> bool:
    """True when the duckdb python package is missing from this environment."""
    return importlib.util.find_spec("duckdb") is None


def ensure_duckdb_extra() -> int:
    """Install the ``[duckdb]`` extra into the active environment when missing."""
    if not duckdb_extra_needed():
        print("[Step 1/5] duckdb python package: already installed")
        return 0
    print('[Step 1/5] Installing the duckdb python package: pip install -e ".[duckdb]"')
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", ".[duckdb]"],
        cwd=".",
        check=False,
    )
    if result.returncode:
        print("  ERROR: the duckdb package did not install. Read the pip output above.")
    return result.returncode


async def provision_analytics(dsn: str) -> int:
    """Create the .duckdb file and apply migrations_duckdb (shared runner)."""
    print(f"[Step 2/5] Analytics file and migrations: {dsn}")
    try:
        await run_migrations(dsn)
    except Exception as exc:
        print(f"  ERROR: migrations failed: {exc}")
        print("  Fix the cause, then re-run corpus-kb setup --backend duckdb.")
        return 1
    return 0


def _sidecar_has_events(sidecar: str) -> bool:
    """True when the sidecar file already holds the library's event tables."""
    with closing(sqlite3.connect(sidecar)) as con:
        rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return "stored_events" in {str(row[0]) for row in rows}


def provision_sidecar(dsn: str) -> int:
    """Create the SQLite event store beside the .duckdb (library-owned schema)."""
    print("[Step 3/5] Event store sidecar (SQLite):")
    try:
        sidecar = events_sidecar_path(dsn)
    except Exception as exc:
        print(f"  ERROR: {exc}")
        return 1
    try:
        already = Path(sidecar).exists() and _sidecar_has_events(sidecar)
    except sqlite3.Error:
        already = False
    if already:
        print(f"  already provisioned: {sidecar}")
        return 0
    try:
        from corpus_kb.domain.application import close_app, get_app

        get_app(dsn, backend="duckdb")
        close_app(("duckdb", dsn))
    except Exception as exc:
        print(f"  ERROR: could not create the event store: {exc}")
        return 1
    print(f"  created: {sidecar}")
    return 0


def preinstall_extensions() -> int:
    """INSTALL + LOAD fts (vss best-effort); failures print the vendoring fix."""
    print("[Step 4/5] Pre-installing the fts extension (downloads once)...")
    import duckdb

    exit_code = 0
    with closing(duckdb.connect(":memory:")) as con:
        for name, required in INSTALL_EXTENSIONS:
            try:
                con.execute(f"INSTALL {name}")
                con.execute(f"LOAD {name}")
            except Exception:
                print(f"  could not download {name}. Offline vendoring:")
                print(f"    On an online machine, run INSTALL {name} with duckdb.")
                print("    Copy the extension file into the folder set as extension_directory.")
                if required:
                    print(f"  Search needs {name}. Fix it, then re-run setup --backend duckdb.")
                    exit_code = 1
                else:
                    print(f"  {name} is optional. The system runs without it.")
            else:
                print(f"  {name}: installed")
    return exit_code


def write_config_pair(config_path: Path, dsn: str) -> int:
    """Write database.backend: duckdb + the DSN into the local config.yaml."""
    print(f"[Step 5/5] Writing {config_path} (database.backend: duckdb)...")
    try:
        print(f"  {write_database_pair(config_path, 'duckdb', dsn)}")
    except ValueError as exc:
        print(f"  ERROR: {exc}")
        return 1
    return 0


def print_duckdb_plan(dsn: str, sidecar: str, config_path: Path, extra_needed: bool) -> None:
    """The exact steps an apply run would execute. Prints only."""
    print("\nThis plan would:")
    if extra_needed:
        print('  1. run: pip install -e ".[duckdb]"')
    else:
        print("  1. keep the installed duckdb python package")
    print(f"  2. create the analytics file and apply migrations: {dsn}")
    print(f"  3. create the SQLite event store: {sidecar}")
    print("  4. pre-install the fts extension (downloads once; vss optional)")
    print(f"  5. write {config_path}: database.backend: duckdb + connection_string")
    print("\nThis was a dry run. Re-run without --dry-run to make changes.")
    print("========================\n")


async def setup_duckdb_cmd(config: dict[str, Any], dry_run: bool, dsn_arg: str = "") -> int:
    """The ``setup --backend duckdb`` flow (§9: setup without Docker)."""
    from corpus_kb._setup import install  # the confirm seam (tests patch install.confirm)

    dsn = resolve_duckdb_dsn(config, dsn_arg)
    try:
        sidecar = events_sidecar_path(dsn)
    except Exception as exc:
        print(f"\nERROR: {exc}")
        return 1
    config_path = config_write_target()

    print("\n=== Corpus-KB DuckDB Setup ===")
    print(f"Analytics file: {dsn}")
    print(f"Event store:    {sidecar}")
    print(f"Config file:    {config_path}")
    if dry_run:
        print_duckdb_plan(dsn, sidecar, config_path, duckdb_extra_needed())
        return 0

    if not install.confirm("Setup will create the two files above and edit the config file."):
        print("Stopped. Nothing was changed.")
        return 0

    exit_code = ensure_duckdb_extra()
    exit_code |= await provision_analytics(dsn)
    exit_code |= provision_sidecar(dsn)
    exit_code |= preinstall_extensions()
    exit_code |= write_config_pair(config_path, dsn)

    print("\nNext steps:")
    print("  corpus-kb doctor   - check the install")
    print("  corpus-kb start    - serve the tools")
    print("Back up the .duckdb file and its .events.db sidecar together.")
    print("========================\n")
    return exit_code


__all__ = [
    "DEFAULT_DUCKDB_FILE",
    "INSTALL_EXTENSIONS",
    "duckdb_extra_needed",
    "ensure_duckdb_extra",
    "preinstall_extensions",
    "provision_analytics",
    "provision_sidecar",
    "resolve_duckdb_dsn",
    "setup_duckdb_cmd",
    "write_config_pair",
]
