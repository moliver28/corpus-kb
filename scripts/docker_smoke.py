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
     the expected shape here (no Ollama in CI): ingest exits 1 with the
     honest ``partial`` envelope while the chunks store, and the lexical
     search arm returns the hit. The ollama profile stays OFF — CI never
     pulls models.
  8. Teardown: ``compose --profile app down -v --remove-orphans`` exit 0.

Exit code 1 on the first failed step with its one-line reason. Output is
bounded and snapshot-friendly: one line per step, tails only on failure.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
import urllib.error
import urllib.request

from docker_smoke_lib import PROJECT, compose_args, dump_diagnostics, run_capture, run_step

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


def exec_app(
    *argv: str,
    timeout: float = EXEC_TIMEOUT_S,
    label: str | None = None,
    check: bool = True,
) -> tuple[int, str, str]:
    """One `docker compose exec -T app <argv>` call."""
    return run_step(
        label or f"exec app: {argv[0]}",
        compose_args(["--profile", "app"], "exec", "-T", "app", *argv),
        timeout,
        check=check,
    )


def round_trip() -> None:
    """Ingest the marker doc, then assert search returns it.

    Degraded honesty: with no Ollama in the container, chunk embeddings
    are all-zero, which is the fatal-marker shape, so ``ingest-text``
    exits 1 with envelope status ``partial`` — that is the EXPECTED
    result here as long as the chunks stored (``pg_chunk_count > 0``)
    and the failure is the embedding arm. Anything else (``error``,
    zero chunks) fails the gate.
    """
    _code, ingest_stdout, _err = exec_app(
        "corpus-kb",
        "ingest-text",
        MARKER_TEXT,
        "--source",
        MARKER,
        "--json",
        label="exec app: ingest-text (degraded partial is expected)",
        check=False,
    )
    envelope: dict[str, object] | None = None
    try:
        parsed = json.loads(ingest_stdout)
        if isinstance(parsed, dict):
            envelope = parsed
    except json.JSONDecodeError:
        pass
    if envelope is None:
        print("[docker-smoke] ingest: FAIL (no JSON envelope on stdout)")
        print(f"[docker-smoke] ingest stdout tail:\n{ingest_stdout[-2000:]}")
        sys.exit(1)
    status = str(envelope.get("status"))
    raw_count = envelope.get("pg_chunk_count")
    chunk_count = int(raw_count) if isinstance(raw_count, (int, float)) else 0
    if status == "success" and chunk_count > 0:
        print(f"[docker-smoke] ingest: ok (success, {chunk_count} chunks stored)")
    elif status == "partial" and chunk_count > 0 and envelope.get("degraded") is True:
        print(
            f"[docker-smoke] ingest: ok (partial, {chunk_count} chunks stored, "
            "embedding arm degraded — the honest no-Ollama shape)"
        )
    else:
        print(f"[docker-smoke] ingest: FAIL (status={status}, chunks={chunk_count})")
        print(f"[docker-smoke] envelope: {json.dumps(envelope)[:1000]}")
        sys.exit(1)
    _code, search_stdout, _err = exec_app("corpus-kb", "search", MARKER, "--json")
    try:
        payload = json.loads(search_stdout)
    except json.JSONDecodeError as exc:
        print(f"[docker-smoke] search json: FAIL ({exc})")
        print(f"[docker-smoke] search stdout tail:\n{search_stdout[-2000:]}")
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
    _code, contract_stdout, _err = run_step(
        "default stack contract",
        ["docker", "compose", "-p", PROJECT, "config", "--services"],
        EXEC_TIMEOUT_S,
    )
    services = {line.strip() for line in contract_stdout.splitlines() if line.strip()}
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

    # 4. Boot with healthchecks as the gate. A failure dumps diagnostics.
    up_code, up_tail = run_capture(
        compose_args(["--profile", "app"], "up", "-d", "--wait", "--wait-timeout", "420"),
        UP_TIMEOUT_S,
    )
    if up_code != 0:
        print(f"[docker-smoke] up --wait: FAIL (exit {up_code})")
        print(f"[docker-smoke] up --wait tail:\n{up_tail}")
        dump_diagnostics()
        sys.exit(1)
    print("[docker-smoke] up --wait: ok")

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
