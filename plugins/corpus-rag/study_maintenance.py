"""Study maintenance: backup, ops inventory, and the op runner command.

``backup`` dumps Postgres as the superuser (tables use FORCE ROW LEVEL
SECURITY, and the ``ag_catalog`` schema is excluded) or copies the DuckDB
files after the server stops. Both paths print the restore command.

Stdlib only.
"""

from __future__ import annotations

import datetime
import json
import shutil
import subprocess
from pathlib import Path

import op_client
import op_runner
import study_commands
import study_config
import study_registry
import study_server
from engine_paths import plugin_data_dir

BACKUP_TIMEOUT = 3600


def _dsn_with_database(dsn: str, database: str) -> str:
    """Replace the database segment of a Postgres DSN path."""
    import urllib.parse

    parts = urllib.parse.urlsplit(dsn)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, ""))


def _backup_postgres(slug: str, study: dict, dest_dir) -> int:
    pg_dump = shutil.which("pg_dump")
    if not pg_dump:
        print("not ok: pg_dump not found. Install the Postgres client tools.")
        return 1
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    dest = dest_dir / f"{slug}-{stamp}.dump"
    dsn = _dsn_with_database(study_config.superuser_dsn(), study_registry.database_name(slug))
    command = [
        pg_dump,
        "--format=custom",
        f"--file={dest}",
        "--exclude-schema=ag_catalog",
        dsn,
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=BACKUP_TIMEOUT, check=False
    )
    if result.returncode != 0:
        print("not ok: pg_dump failed.")
        detail = (result.stderr or result.stdout).strip()
        if detail:
            print("\n".join(detail.splitlines()[-8:]))
        return 1
    app_dsn = study_commands.mask_dsn(str(study.get("database_url", "")))
    print(f"ok: backup written to {dest}.")
    print(f"Restore: pg_restore --dbname=\"<study database>\" {dest}")
    print(f"(The study's app DSN is {app_dsn}; the dump excludes schema ag_catalog.)")
    return 0


def _backup_duckdb(slug: str, study: dict, dest_dir) -> int:
    study_server.stop_before(slug, study, "backup")
    db_file = study_registry.duckdb_file(slug)
    if not db_file.is_file():
        print(f"not ok: no DuckDB file at {db_file} for '{slug}'.")
        return 1
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    copied = []
    for suffix in ("", ".wal", ".events.db"):
        source = Path(str(db_file) + suffix)
        if not source.is_file():
            continue
        dest = dest_dir / f"{db_file.name}-{stamp}{suffix}"
        shutil.copyfile(source, dest)
        copied.append(dest.name)
    print(f"ok: copied {', '.join(copied)} into {dest_dir}.")
    print("Restore: stop the study server, then copy the files back into")
    print(f"  {db_file.parent}")
    return 0


def cmd_backup(args) -> int:
    slug, study = study_commands.scoped_study_off(args.study_slug)
    dest_dir = plugin_data_dir() / "backups" / slug
    dest_dir.mkdir(parents=True, exist_ok=True)
    if str(study.get("backend", "postgres")) == "duckdb":
        return _backup_duckdb(slug, study, dest_dir)
    return _backup_postgres(slug, study, dest_dir)


def cmd_ops(args) -> int:
    """The live op inventory, one line per op, with the live count."""
    cli = study_commands.require_engine()
    inventory = op_runner.load_inventory(cli)
    ops = inventory.get("operations", [])
    if args.family:
        ops = [row for row in ops if row.get("family") == args.family]
        if not ops:
            print(f"not ok: no ops in family '{args.family}'. Run `corpus-rag ops`.")
            return 1
    for row in sorted(ops, key=lambda r: (str(r.get("family")), str(r.get("name")))):
        print(f"{row.get('family')}/{row.get('name')}  {row.get('description')}")
    scope = f" in family '{args.family}'" if args.family else ""
    print(f"\n{len(ops)} operations{scope}. Run one: corpus-rag op <name> key=value")
    return 0


def cmd_op(args) -> int:
    """Run any op against the study server, validated against the inventory."""
    cli, _slug, study = study_commands.scoped_study(args.study_slug)
    try:
        envelope = op_runner.run_op(
            cli, args.name, args.pairs, study_server.base_url(study), json_blob=args.json
        )
    except op_runner.OpError as exc:
        print(f"not ok: {exc}")
        return 2
    except op_client.HttpError as exc:
        print(f"not ok: {exc}")
        return 1
    print(json.dumps(envelope, indent=2, default=str))
    status = envelope.get("status", "success")
    return 0 if status == "success" else 1
