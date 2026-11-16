#!/usr/bin/env python3
"""Docker compose smoke gate: build, boot, and exercise the full app stack.

Zero-drift rule 10 three-part: poe target ``docker-smoke``, the ci.yml
``docker`` job, and the enforcement-map row in
``docs/research/harness-zero-drift-program.md``.

Steps, each bounded with one result line:
  0. Skip honestly when the docker CLI is absent (Windows dev boxes and
     Windows CI have no docker; the ubuntu CI leg enforces this gate).
  1. Default-stack contract: ``compose config --services`` with no profile
     active must list exactly ``postgres`` (compose.yaml default unchanged).
  2. Clean slate: ``compose --profile app down -v --remove-orphans``.
  3. Build: ``compose --profile app build`` (wheel self-check runs inside
     the image build).
  4. Boot: ``compose --profile app up -d --wait`` (healthchecks gate).
  5. Host health poll: GET http://127.0.0.1:8010/app/ until 200 — the
     LAN-shaped path a NAS user takes.
  6. Doctor: ``compose exec -T app corpus-kb doctor`` exit 0.
  7. Round trip: ``ingest-text`` a marker document, then ``search --json``
     and assert the marker text comes back. Zero-vector degraded mode is
     the expected shape here (no Ollama in CI): the lexical arm returns
     the hit and the envelope reports it. The ollama profile stays OFF —
     CI never pulls models.
  8. Teardown: ``compose --profile app down -v --remove-orphans`` exit 0.

Exit code 1 on the first failed step with its one-line reason. Output is
bounded and snapshot-friendly: one line per step, tails only on failure.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MARKER = "docker-smoke-nas-canary"

MARKER_TEXT = (
    "docker-smoke-nas-canary: The smallest library on earth holds exactly "
    "three books and one reading lamp."
)

APP_URL = "http://127.0.0.1:8010/app/"

BUILD_TIMEOUT_S = 900.0
UP_TIMEOUT_S = 480.0
EXEC_TIMEOUT_S = 180.0
HEALTH_TRIES = 45
HEALTH_DELAY_S = 2.0


def compose_env() -> dict[str, str]:
    """A scrubbed env: compose input is fully deterministic.

    A developer's COMPOSE_PROFILES / COMPOSE_FILE / COMPOSE_PROJECT_NAME
    must not flip what this gate builds or asserts. Host CORPUS_KB_* vars
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


def run_step(
    label: str,
    args: list[str],
    timeout: float,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """One compose/docker subprocess; one result line; tail on failure."""
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
        sys.exit(1)
    if check and proc.returncode != 0:
        print(f"[docker-smoke] {label}: FAIL (exit {proc.returncode})")
        for name in ("stdout", "stderr"):
            out = getattr(proc, name) or ""
            tail = "\n".join(out.strip().splitlines()[-25:])
            if tail:
                print(f"[docker-smoke] {label} {name} tail:\n{tail}")
        sys.exit(1)
    print(f"[docker-smoke] {label}: ok")
    return proc


def compose_args(profile_arg: list[str], *rest: str) -> list[str]:
    return ["docker", "compose", "-p", "corpus_kb_smoke", *profile_arg, *rest]


def poll_health() -> None:
    """GET /app/ from the HOST until 200 (the LAN-shaped assertion)."""
    deadline = time.monotonic() + HEALTH_TRIES * HEALTH_DELAY_S
    last = "no attempt"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(APP_URL, timeout=5) as resp:
                if resp.status == 200:
                    print(f"[docker-smoke] host GET {APP_URL}: ok (200)")
                    return
                last = f"status {resp.status}"
        except (urllib.error.URLError, OSError) as exc:
            last = str(exc)
        time.sleep(HEALTH_DELAY_S)
    print(f"[docker-smoke] host GET {APP_URL}: FAIL ({last})")
    sys.exit(1)


def exec_app(*argv: str, timeout: float = EXEC_TIMEOUT_S) -> subprocess.CompletedProcess[str]:
    """One `docker compose exec -T app <argv>` call."""
    return run_step(
        f"exec app: {argv[0]}",
        compose_args(["--profile", "app"], "exec", "-T", "app", *argv),
        timeout,
    )


def round_trip() -> None:
    """Ingest the marker doc, then assert search returns it."""
    exec_app("corpus-kb", "ingest-text", MARKER_TEXT, "--source", MARKER)
    proc = exec_app("corpus-kb", "search", MARKER, "--json")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        print(f"[docker-smoke] search json: FAIL ({exc})")
        print(f"[docker-smoke] search stdout tail:\n{proc.stdout[-2000:]}")
        sys.exit(1)
    hits = payload.get("result")
    if not isinstance(hits, list) or not hits:
        print("[docker-smoke] search: FAIL (no hits in the envelope)")
        print(f"[docker-smoke] envelope: {json.dumps(payload)[:1000]}")
        sys.exit(1)
    for hit in hits:
        if MARKER in str(hit.get("text", "")):
            print(f"[docker-smoke] search: ok ({len(hits)} hit(s), marker returned)")
            return
    print("[docker-smoke] search: FAIL (hits came back, but none carries the marker)")
    print(f"[docker-smoke] first hit: {json.dumps(hits[0])[:600]}")
    sys.exit(1)


def main(argv: list[str] | None = None) -> int:
    """Run the gate; exit 0 on pass, 1 on the first failure."""
    del argv  # the gate takes no options; poe calls it bare
    if shutil.which("docker") is None:
        print("[docker-smoke] SKIP: no docker CLI on this machine (gate enforced in ubuntu CI).")
        return 0

    # 1. Default-stack contract: no profile -> exactly {postgres}.
    proc = run_step(
        "default stack contract",
        ["docker", "compose", "-p", "corpus_kb_smoke", "config", "--services"],
        EXEC_TIMEOUT_S,
    )
    services = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    if services != {"postgres"}:
        print(f"[docker-smoke] default stack contract: FAIL (services = {sorted(services)})")
        return 1
    print("[docker-smoke] default stack contract: ok (postgres only)")

    # 2. Clean slate (ignored failures: nothing up yet is fine).
    run_step(
        "pre-clean",
        compose_args(["--profile", "app"], "down", "-v", "--remove-orphans"),
        EXEC_TIMEOUT_S,
        check=False,
    )

    # 3. Build (the wheel self-check lives inside the Dockerfile).
    run_step(
        "build app image",
        compose_args(["--profile", "app"], "build"),
        BUILD_TIMEOUT_S,
    )

    # 4. Boot with healthchecks as the gate.
    run_step(
        "up --wait",
        compose_args(["--profile", "app"], "up", "-d", "--wait", "--wait-timeout", "420"),
        UP_TIMEOUT_S,
    )

    # 5. Host-side health poll (the LAN path).
    poll_health()

    # 6. Doctor must answer clean inside the container.
    exec_app("corpus-kb", "doctor")

    # 7. Ingest + search round trip (degraded mode is the honest norm).
    round_trip()

    # 8. Teardown must be clean.
    run_step(
        "down -v",
        compose_args(["--profile", "app"], "down", "-v", "--remove-orphans"),
        EXEC_TIMEOUT_S,
    )

    print("[docker-smoke] PASS.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
