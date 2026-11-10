"""Study admin commands: provision, doctor, migrate, reembed (issue #351).

``provision`` creates, migrates, and registers a study, and adopts an
existing database or file. The 0.7.x raw-table ``provision`` now lives
as ``raw-provision``; a ``provision`` call that carries ``--project-id``
still routes there, so old scripts do not break silently.

Stdlib only.
"""

from __future__ import annotations

import json
import subprocess

import op_client
import op_runner
import study_commands
import study_config
import study_registry
import study_server

PROVISION_TIMEOUT = 3600
MIGRATE_TIMEOUT = 1800


def cmd_provision(args) -> int:
    if getattr(args, "project_id", None):
        return cmd_raw_provision(args)
    return _provision_study(args)


def _cleanup_hint(backend: str, slug: str) -> str:
    if backend == "duckdb":
        db_file = study_registry.duckdb_file(slug)
        return (
            "Cleanup: delete the created files if any: "
            f"{db_file} (and {db_file}.wal, {db_file}.events.db)."
        )
    dsn = study_commands.mask_dsn(study_config.superuser_dsn())
    return (
        "Cleanup: drop the database with:\n"
        f'  psql "{dsn}" '
        f"-c 'DROP DATABASE IF EXISTS {study_registry.database_name(slug)};'"
    )


def _provision_study(args) -> int:
    cli = study_commands.require_engine()
    slug = study_registry.slugify(args.slug)
    registry = study_registry.load_registry()
    if slug in registry.get("studies", {}):
        print(f"not ok: a study named '{slug}' already exists. Run `corpus-rag list`.")
        return 1
    backend = args.backend or study_config.default_backend()
    if backend not in ("postgres", "duckdb"):
        print("not ok: --backend must be postgres or duckdb.")
        return 2
    port = study_registry.allocate_port(registry)
    if backend == "duckdb":
        db_file = study_registry.duckdb_file(slug)
        db_file.parent.mkdir(parents=True, exist_ok=True)
        database_url = f"duckdb:///{db_file}"
        command = [cli, "setup", "--backend", "duckdb", "--dsn", str(db_file)]
    else:
        database_url = study_config.study_dsn(study_registry.database_name(slug))
        command = [
            cli,
            "quickstart",
            "--dsn",
            database_url,
            "--no-write",
            "--json",
        ]
    env = study_config.study_env(
        slug, {"database_url": database_url, "backend": backend}
    )
    if backend == "postgres":
        # The engine reads this fallback; argv would show the DSN to every
        # process list for the whole provisioning run.
        env["CORPUS_KB_SUPERUSER_DSN"] = study_config.superuser_dsn()
    print(f"Provisioning study '{slug}' on the {backend} backend (port {port})...")
    result = subprocess.run(
        command, env=env, capture_output=True, text=True,
        timeout=PROVISION_TIMEOUT, check=False,
    )
    output = (result.stdout or result.stderr).strip()
    if result.returncode != 0:
        print("not ok: provisioning failed. The engine said:")
        if output:
            print("\n".join(output.splitlines()[-12:]))
        print(_cleanup_hint(backend, slug))
        return 1
    study_registry.register_study(registry, slug, database_url, port, backend)
    study_registry.save_registry(registry)
    print(f"ok: study '{slug}' is ready on the {backend} backend, port {port}.")
    print(f"Database: {study_commands.mask_dsn(database_url)}")
    print(f"Make it active: corpus-rag use {slug}")
    return 0


def cmd_raw_provision(args) -> int:
    """The 0.7.x Track C raw table, renamed from ``provision``."""
    cli = study_commands.require_engine()
    command = [cli, "project-provision", args.slug, "--json"]
    if args.project_id:
        command += ["--project-id", args.project_id]
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=120, check=False
    )
    output = (result.stdout or result.stderr).strip()
    if result.returncode != 0:
        print("not ok: project-provision failed.")
        if output:
            print(output)
        print("Fix: run `corpus-kb doctor` and read the lines above.")
        return 1
    try:
        envelope = json.loads(output[output.index("{"):])
    except (ValueError, IndexError):
        print(output)
        return 0
    print(f"ok: raw table {envelope.get('table')} ready on the {envelope.get('backend')} backend.")
    if envelope.get("already_provisioned"):
        print("This project already had its raw table; nothing changed.")
    return 0


def cmd_doctor(args) -> int:
    """Engine diagnostics with the study's database and overlay."""
    cli = study_commands.require_engine()
    slug, study = study_commands.scoped_study_off(args.study_slug)
    study_server.stop_before(slug, study, "doctor")
    result = subprocess.run(
        [cli, "doctor", "--json"],
        env=study_config.study_env(slug, study),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    output = (result.stdout or result.stderr).strip()
    if output:
        print(output)
    if result.returncode == 3:
        print(f"not ok: the '{slug}' database is unreachable.")
        return 3
    if result.returncode != 0:
        print(f"not ok: doctor reported a problem for '{slug}'.")
        return result.returncode or 1
    return 0


def cmd_migrate(args) -> int:
    """Pending migrations per study, or all studies with --all."""
    cli = study_commands.require_engine()
    registry = study_registry.load_registry()
    if args.all:
        targets = sorted(registry.get("studies", {}).items())
        if not targets:
            print("No studies to migrate. Create one: corpus-rag provision <slug>")
            return 1
    else:
        slug_arg = args.study_slug or getattr(args, "study_slug_flag", None)
        targets = [study_commands.scoped_study_off(slug_arg)]
    worst = 0
    for slug, study in targets:
        code = _migrate_one(cli, args, slug, study)
        worst = max(worst, code)
    return worst


def _migrate_one(cli: str, args, slug: str, study: dict) -> int:
    study_server.stop_before(slug, study, "migrate")
    command = [cli, "migrate", "--json"]
    if getattr(args, "reset_embeddings", False):
        command.append("--reset-embeddings")
    env = study_config.study_env(slug, study)
    if str(study.get("backend", "postgres")) == "postgres":
        # The engine reads this fallback; argv would show the DSN to every
        # process list for the whole (up to 30 minute) migration.
        env["CORPUS_KB_SUPERUSER_DSN"] = study_config.superuser_dsn()
    result = subprocess.run(
        command,
        env=env,
        capture_output=True,
        text=True,
        timeout=MIGRATE_TIMEOUT,
        check=False,
    )
    if result.returncode != 0:
        print(f"not ok: migrate failed for '{slug}'.")
        output = (result.stdout or result.stderr).strip()
        if output:
            print("\n".join(output.splitlines()[-8:]))
        return result.returncode or 1
    _print_migrate_report(slug, result.stdout)
    return 0


def _print_migrate_report(slug: str, stdout: str) -> None:
    try:
        report = json.loads(stdout)
    except ValueError:
        print(f"{slug}: migrate finished.")
        return
    applied_now = report.get("applied_now") or []
    pending = report.get("pending") or []
    print(f"{slug}: applied now {len(applied_now)}; pending {len(pending)}.")
    if pending:
        print(f"  Pending: {', '.join(map(str, pending))}")


def cmd_reembed(args) -> int:
    """Rebuild missing vectors; DuckDB stops its server first.

    Postgres runs the ``ingest_reembed`` op over HTTP. DuckDB runs the
    engine CLI with the study env after the server stops, because the
    file allows one writer.
    """
    cli = study_commands.require_engine()
    duck_slug, duck_study = study_commands.scoped_study_off(args.study_slug)
    if str(duck_study.get("backend", "")) == "duckdb":
        study_server.stop_before(duck_slug, duck_study, "reembed")
        command = [cli, "ingest-reembed"]
        if args.limit is not None:
            command += ["--limit", str(args.limit)]
        result = subprocess.run(
            command,
            env=study_config.study_env(duck_slug, duck_study),
            capture_output=True,
            text=True,
            timeout=7200,
            check=False,
        )
        output = (result.stdout or result.stderr).strip()
        if output:
            print(output)
        if result.returncode != 0:
            print(f"not ok: reembed failed for '{duck_slug}'.")
            return result.returncode or 1
        return 0
    _cli, slug, study = study_commands.scoped_study(args.study_slug)
    pairs = [] if args.limit is None else [f"limit={args.limit}"]
    try:
        envelope = op_runner.run_op(cli, "ingest_reembed", pairs, study_server.base_url(study))
    except op_runner.OpError as exc:
        print(f"not ok: {exc}")
        return 2
    except op_client.HttpError as exc:
        print(f"not ok: {exc}")
        return 1
    print(
        f"ok: reembed on '{slug}' scanned {envelope.get('scanned')}, "
        f"fixed {envelope.get('fixed')}, failed {envelope.get('failed')}."
    )
    return 0
