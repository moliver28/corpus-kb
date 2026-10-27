#!/usr/bin/env python3
"""SPA e2e orchestrator (UI SPA program Task 9, gate `poe ui-e2e`).

Boots a REAL corpus-kb server on a throwaway database, preloads the
demo corpus, runs the Playwright smoke + axe scan, then tears both
down. Requires: a reachable Postgres (tests/research_db.py recipe),
the venv console script (corpus-kb), pnpm, and the Playwright chromium
browser (`pnpm -C ui_src exec playwright install chromium` once).

Exit code: the Playwright run's exit code (0 = smoke green).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))

os.environ.setdefault("CORPUS_KB_TEST_DB", "corpus_kb_e2e")

import research_db  # noqa: E402  (tests/ helper; shared bootstrap)

PORT = 8010
BASE = f"http://localhost:{PORT}"



def wait_for_app(timeout_s: int = 90) -> None:
    """Poll /app/ until the SPA mount answers."""
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE}/app/", timeout=5) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError) as exc:  # connection refused, etc.
            last_error = exc
        time.sleep(1.0)
    raise RuntimeError(f"the server never answered on {BASE}/app/: {last_error}")


def preload_demo() -> None:
    """POST /api/demo/load (idempotent; embeds the five sample files)."""
    request = urllib.request.Request(
        f"{BASE}/api/demo/load", data=b"{}", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        body = response.read().decode("utf-8", errors="replace")
    print(f"demo preload: HTTP {response.status} {body[:120]}")


def run_playwright() -> int:
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        raise RuntimeError("pnpm not found on PATH (the poe ui-* targets export it)")
    return subprocess.call(
        [pnpm, "-C", "ui_src", "exec", "playwright", "test"], cwd=str(REPO)
    )


def main() -> int:
    dsn = asyncio.run(research_db.create_research_db())
    print(f"throwaway database ready: {dsn}")
    server = None
    log_handle = None
    try:
        # The venv console script wins: a global corpus-kb shim (e.g. an
        # Anaconda Scripts entry) cannot import the project package.
        exe = str(Path(sys.executable).parent / "corpus-kb.exe")
        if not Path(exe).is_file():
            exe = shutil.which("corpus-kb") or exe
        env = {**os.environ, "CORPUS_KB_DATABASE_URL": dsn}
        # A just-killed server can leave port 8010 unbindable for a few
        # seconds on Windows; retry the start instead of failing the run.
        server_log = REPO / "ui_src" / "test-results" / "server.log"
        server_log.parent.mkdir(parents=True, exist_ok=True)
        # Open once in append mode: failed attempts keep their output for
        # the artifact upload instead of truncating each retry.
        log_handle = server_log.open("a")
        for attempt in range(1, 4):
            log_handle.write(f"--- ui_e2e server start (attempt {attempt}) ---\n")
            log_handle.flush()
            server = subprocess.Popen(
                [exe, "start", "--transport", "http", "--port", str(PORT)],
                env=env,
                cwd=str(REPO),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
            )
            try:
                wait_for_app()
                break
            except RuntimeError:
                if attempt == 3:
                    server.terminate()
                    log_handle.close()
                    raise
                server.terminate()
                server.wait(timeout=15)
                time.sleep(2)
        preload_demo()
        return run_playwright()
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
        if log_handle is not None and not log_handle.closed:
            log_handle.close()
        research_db.set_env(dsn)
        try:
            asyncio.run(research_db.drop_research_db())
        finally:
            research_db.unset_env()
        print("teardown complete")


if __name__ == "__main__":
    sys.exit(main())
