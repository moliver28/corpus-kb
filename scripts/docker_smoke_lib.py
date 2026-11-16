"""Shared plumbing for the docker compose smoke gate.

Split out of ``docker_smoke.py`` (size-ratchet F11): the compose env
scrub, the deterministic project naming, and the failure forensics live
here so the gate script stays under its recorded line ceiling. The gate
invokes it through ``python -m scripts.docker_smoke`` — the ``-m`` form
keeps the repo root on sys.path so this sibling import resolves.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Fixed compose project: reruns replace exactly this stack, and the CI
#: leftover check filters on the same name.
PROJECT = "corpus_kb_smoke"


def compose_env() -> dict[str, str]:
    """A scrubbed env: compose input is fully deterministic.

    A developer's COMPOSE_PROFILES / COMPOSE_FILE / COMPOSE_PROJECT_NAME
    must not flip what the gate builds or asserts. Host CORPUS_KB_* vars
    are also dropped: the container config comes from compose.yaml alone.
    """
    scrub = {
        "COMPOSE_PROFILES",
        "COMPOSE_FILE",
        "COMPOSE_PROJECT_NAME",
        "COMPOSE_PATH_SEPARATOR",
    }
    env = {k: v for k, v in os.environ.items() if not k.startswith("CORPUS_KB_")}
    for key in scrub:
        env.pop(key, None)
    return env


def compose_args(profile_arg: list[str], *rest: str) -> list[str]:
    """One compose argv: fixed project name, then profile, then command."""
    return ["docker", "compose", "-p", PROJECT, *profile_arg, *rest]


def run_capture(args: list[str], timeout: float, tail_lines: int = 60) -> tuple[int, str]:
    """One subprocess; (exit code, combined output tail-truncated)."""
    try:
        proc = subprocess.run(
            args,
            cwd=ROOT,
            env=compose_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, "\n".join(out.strip().splitlines()[-tail_lines:])


def dump_diagnostics() -> None:
    """Bounded failure forensics: why is the app container not healthy?"""
    probes: dict[str, list[str]] = {
        "compose ps -a": compose_args(["--profile", "app"], "ps", "-a"),
        "app health log": [
            "docker",
            "inspect",
            "--format",
            "{{json .State.Health}}",
            "corpus-kb-app",
        ],
        "app logs (first 40)": compose_args(["--profile", "app"], "logs", "--tail", "220", "app"),
        "postgres logs (last 30)": compose_args(
            ["--profile", "app"], "logs", "--tail", "30", "postgres"
        ),
        "db state": [
            "docker",
            "compose",
            "-p",
            PROJECT,
            "--profile",
            "app",
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "corpus_user",
            "-d",
            "corpus_kb",
            "-c",
            "SELECT filename FROM corpus.schema_migrations ORDER BY filename",
            "-c",
            "SELECT table_name FROM information_schema.tables"
            " WHERE table_schema IN ('corpus','public') ORDER BY table_name",
            "-c",
            "SELECT extname FROM pg_extension ORDER BY extname",
        ],
    }
    for label, args in probes.items():
        _, tail = run_capture(args, 90)
        if label == "app logs (first 40)":
            tail = "\n".join(tail.splitlines()[:40])
        print(f"[docker-smoke] diagnostics {label}:\n{tail}")


def run_step(
    label: str,
    args: list[str],
    timeout: float,
    check: bool = True,
) -> tuple[int, str, str]:
    """One compose/docker subprocess; one result line; tail on failure.

    Returns (exit code, stdout, stderr) with the FULL streams — callers
    that parse output (the JSON round trip) need them untruncated. On a
    checked failure it prints the bounded tail and exits the process.
    """
    print(f"[docker-smoke] {label} ...", flush=True)
    try:
        proc = subprocess.run(
            args,
            cwd=ROOT,
            env=compose_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        print(f"[docker-smoke] {label}: FAIL (timed out after {timeout:.0f}s)")
        raise SystemExit(1) from None
    if check and proc.returncode != 0:
        print(f"[docker-smoke] {label}: FAIL (exit {proc.returncode})")
        for name in ("stdout", "stderr"):
            out = getattr(proc, name) or ""
            tail = "\n".join(out.strip().splitlines()[-25:])
            if tail:
                print(f"[docker-smoke] {label} {name} tail:\n{tail}")
        raise SystemExit(1)
    print(f"[docker-smoke] {label}: ok")
    return proc.returncode, proc.stdout or "", proc.stderr or ""
