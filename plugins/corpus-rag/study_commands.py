"""Study lifecycle commands and shared resolution helpers (issue #351).

Every per-study command resolves the engine and the study (4-step
selection), notes use, reaps idle servers, and turns the study server up
when it needs HTTP. Errors raise ``CommandError``; ``main`` prints them
as one honest line.

Stdlib only.
"""

from __future__ import annotations

import contextlib
import json
import re
import subprocess
import sys

import op_client
import study_config
import study_registry
import study_server
from engine_paths import engine_cli, engine_root, plugin_data_dir

NOT_FOUND = (
    "Corpus-KB engine not found. Searched the engine_root userConfig, "
    "$CORPUS_KB_REPO, a dev checkout two folders up, and <state>/engine. "
    "See docs/PLUGIN.md in the repository for install steps."
)


class CommandError(Exception):
    """A user-facing command failure; main prints the message."""


def require_engine() -> str:
    """The engine CLI command line, or a typed error."""
    root = engine_root()
    if root is None:
        raise CommandError(NOT_FOUND)
    cli = engine_cli(root)
    if cli is None:
        raise CommandError(
            f"Engine checkout found at {root}, but no corpus-kb command in it "
            "or on PATH.\nFix: run `uv sync --frozen --extra dev` inside that checkout."
        )
    return cli


def scoped_study(
    slug_arg: str | None, ensure_server: bool = True
) -> tuple[str, dict, str]:
    """Engine CLI plus the selected study, ready to use.

    Notes use, reaps other studies' idle servers, and (by default) turns
    the study's server up. Returns ``(cli, slug, study)``.
    """
    cli = require_engine()
    registry = study_registry.load_registry()
    slug, study = study_registry.resolve_study(registry, slug_arg)
    for reaped in study_server.note_use_and_reap(registry, slug):
        print(f"Stopped the idle server for '{reaped}' (idle over 30 minutes).")
    if ensure_server:
        study = study_server.ensure_running(cli, registry, slug, study)
    return cli, slug, study


def scoped_study_off(slug_arg: str | None) -> tuple[str, dict]:
    """The selected study without touching any server."""
    registry = study_registry.load_registry()
    return study_registry.resolve_study(registry, slug_arg)


def mask_dsn(dsn: str) -> str:
    """Hide the password part of a DSN before printing it."""
    return re.sub(r"(://[^:@/]+:)[^@]+(@)", r"\1***\2", dsn)


def cmd_list(_args) -> int:
    registry = study_registry.load_registry()
    studies = registry.get("studies", {})
    if not studies:
        print("No studies yet. Create one: corpus-rag provision <slug>")
        return 0
    active = registry.get("active")
    for slug in sorted(studies):
        study = studies[slug]
        port = int(study.get("port") or 0)
        up = study_server.pid_alive(study.get("pid")) and study_server.probe(port, timeout=2.0)
        mark = "*" if slug == active else " "
        print(
            f"{mark} {slug}  {study.get('backend', 'postgres')}  port {port}  "
            f"{'up' if up else 'down'}  {mask_dsn(str(study.get('database_url', '')))}"
        )
    print("\n* marks the active study.")
    return 0


def cmd_use(args) -> int:
    def activate(registry: dict) -> None:
        study_registry.set_active(registry, args.slug)

    study_registry.mutate_registry(activate)
    print(f"Active study: {args.slug}")
    return 0


def cmd_serve(args) -> int:
    _cli, slug, study = scoped_study(args.study_slug)
    print(
        f"Study '{slug}' serves on http://127.0.0.1:{study.get('port')} "
        f"(pid {study.get('pid')})."
    )
    return 0


def cmd_stop(args) -> int:
    registry = study_registry.load_registry()
    slug, study = study_registry.resolve_study(registry, args.study_slug)
    if study_server.stop_study(slug, study):
        study_registry.save_registry(registry)
        print(f"Stopped the '{slug}' server.")
    else:
        print(f"The '{slug}' server is not running.")
    return 0


def cmd_connect(_args) -> int:
    """One MCP entry per study with its env, plus the default entry."""
    root = engine_root()
    cli = engine_cli(root) if root is not None else None
    command = cli if cli else "corpus-kb"
    if cli is None:
        print(NOT_FOUND, file=sys.stderr)
        print("Printing a config that expects `corpus-kb` on PATH.", file=sys.stderr)
    config: dict = {
        "mcpServers": {"corpus-kb": {"command": command, "args": ["--transport", "stdio"]}}
    }
    registry = study_registry.load_registry()
    for slug, study in sorted(registry.get("studies", {}).items()):
        env = {
            key: value
            for key, value in study_config.study_env(slug, study).items()
            if key.startswith("CORPUS_KB_")
        }
        config["mcpServers"][f"corpus-kb-{slug}"] = {
            "command": command,
            "args": ["--transport", "stdio"],
            "env": env,
        }
    print(json.dumps(config, indent=2))
    return 0


def _run_doctor_json(cli: str, slug: str, study: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [cli, "doctor", "--json"],
        env=study_config.study_env(slug, study),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def _print_hook_errors() -> None:
    log = plugin_data_dir() / "hook-errors.jsonl"
    try:
        count = len(log.read_text(encoding="utf-8").splitlines())
    except OSError:
        return
    if count:
        print(f"New hook errors: {count} (see {log}).")


def _print_since_last_status() -> None:
    path = plugin_data_dir() / "since_status.json"
    counts = {"ingested": 0, "failed": 0}
    if path.is_file():
        with contextlib.suppress(OSError, ValueError):
            counts.update(json.loads(path.read_text(encoding="utf-8")))
    print(f"Since last status: {counts['ingested']} ingested, {counts['failed']} failed.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ingested": 0, "failed": 0}), encoding="utf-8")


def cmd_status(args) -> int:
    """Per-study status with the false-ok fix (issue #351).

    The study's doctor decides the verdict; exit 3 means the database is
    unreachable, and the report says so. Counts come from /api/stats.
    """
    cli = require_engine()
    registry = study_registry.load_registry()
    try:
        slug, study = study_registry.resolve_study(registry, args.study_slug)
    except study_registry.RegistryError as exc:
        root = engine_root()
        if root is not None:
            print(f"Engine: {root}")
        raise CommandError(str(exc)) from exc
    print(f"Study: {slug}")
    print(f"Database: {mask_dsn(str(study.get('database_url', '')))} ({study.get('backend')})")
    study_server.note_use_and_reap(registry, slug)
    doctor = _run_doctor_json(cli, slug, study)
    if doctor.returncode == 3:
        print("not ok: the study database is unreachable.")
        return 1
    if doctor.returncode != 0:
        print("not ok: doctor reported a problem.")
        output = (doctor.stdout or doctor.stderr).strip()
        if output:
            print("\n".join(output.splitlines()[-10:]))
        return 1
    _print_doctor_warnings(doctor.stdout)
    study = study_server.ensure_running(cli, registry, slug, study)
    try:
        stats = op_client.get_json(study_server.base_url(study) + "/api/stats", timeout=30)
    except op_client.HttpError as exc:
        print(f"not ok: the study server answered but stats failed: {exc}")
        return 1
    for key in ("documents", "chunks", "chunks_vectors", "entities", "relations", "tags"):
        if key in stats:
            print(f"{key}: {stats[key]}")
    _print_queue_count(study)
    _print_hook_errors()
    _print_since_last_status()
    print("ok: doctor passed; the study answers.")
    return 0


def _print_doctor_warnings(doctor_json: str) -> None:
    try:
        report = json.loads(doctor_json)
    except ValueError:
        return
    checks = report.get("checks") if isinstance(report, dict) else None
    ollama = checks.get("ollama") if isinstance(checks, dict) else None
    if isinstance(ollama, dict) and ollama.get("reachable") is False:
        print("Ollama is down; keyword search only. Start it, then re-run status.")


def _print_queue_count(study: dict) -> None:
    """Best-effort queue line; the queue wave owns the full picture."""
    try:
        report = op_client.post_json(
            study_server.base_url(study) + "/api/queue/status", {"limit": 200}, timeout=30
        )
    except (op_client.HttpError, ValueError):
        return
    rows = report.get("result")
    if isinstance(rows, list):
        print(f"Queue: {len(rows)} row(s).")
