#!/usr/bin/env python3
"""PyPI wheel smoke gate: install the PUBLISHED wheel fresh, prove the CLI boots.

Zero-drift rule 10 three-part: poe target ``pypi-smoke``, the ci.yml
``pypi-smoke`` job (ubuntu/windows/macos), and the enforcement-map row
in ``docs/research/harness-zero-drift-program.md``.

The gate installs the published ``corpus-kb`` wheel from PyPI, UNPINNED,
into a throwaway venv, so it tracks whatever is latest at run time and
depends on no branch code. It then proves the CLI boots on that clean
install:

  1. ``corpus-kb --help``         exit 0, output carries the "Usage" marker.
  2. ``corpus-kb doctor``         exit 0 or 3. 3 is the honest "database
     unreachable" degraded state; no database exists on runners.
  3. ``corpus-kb demo-kb --help`` exit 0.

Assertions are string-agnostic on purpose: exit codes plus the one
"Usage" marker only — help copy drifts between releases.

Offline case: a quick PyPI reachability probe first. When PyPI is not
reachable the gate prints one ``SKIP: PyPI not reachable`` line and
exits 0 (the declared skip semantics, the same honesty as
``docker-smoke``). An HTTP error status (the index answers but the
package is gone) is a real failure, not a skip; a transient 5xx gets
one retry with a short backoff, a malformed 200 body fails bounded.

Exit code 1 on the first real failure with a bounded reason. The temp
venv self-cleans on success AND failure. Output stays bounded: one line
per step, tails only on failure.
"""

from __future__ import annotations

import http.client
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

PACKAGE = "corpus-kb"
PYPI_JSON_URL = f"https://pypi.org/pypi/{PACKAGE}/json"
PROBE_TIMEOUT_S = 15.0
#: Backoff before the one retry of a transient index 5xx (momus P2).
PROBE_RETRY_BACKOFF_S = 5.0
#: Overall wall-clock guard. The whole gate must fit inside this budget:
#: the ~100 dependency wheels install inside it on every CI OS. The CI
#: job cap sits above it (windows installs slowest).
BUDGET_S = 1080.0  # 18 minutes
INSTALL_TIMEOUT_S = 900.0  # one pip step
CLI_TIMEOUT_S = 180.0  # one CLI call (doctor may probe a dead socket)
MARKER = "Usage"  # the only output string the gate asserts on
PREFIX = "[pypi-smoke]"


def _remaining(deadline: float) -> float:
    """Seconds left in the overall budget; fail honestly when it ran out."""
    left = deadline - time.monotonic()
    if left <= 0:
        print(
            f"{PREFIX} FAIL: the {BUDGET_S:.0f}s overall budget ran out before this step.",
            flush=True,
        )
        raise SystemExit(1)
    return left


def _run(args: list[str], timeout: float, tail_lines: int = 40) -> tuple[int, str]:
    """One subprocess; (exit code, combined-output tail). Timeout -> 124."""
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout:.0f}s"
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = "\n".join(out.strip().splitlines()[-tail_lines:])
    return proc.returncode, tail


def _fail(label: str, tail: str) -> None:
    """One FAIL line plus a bounded tail, then exit 1 (first failure wins)."""
    print(f"{PREFIX} {label}: FAIL", flush=True)
    if tail:
        print(f"{PREFIX} {label} detail (tail):\n{tail}")
    raise SystemExit(1)


def _probe_once() -> object:
    """One GET of the package index; returns the parsed JSON body."""
    with urllib.request.urlopen(PYPI_JSON_URL, timeout=PROBE_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _probe_pypi() -> None:
    """Reachability probe. SKIP (exit 0) when PyPI is not reachable.

    One retry with a short backoff on a transient 5xx (502/503 blips);
    a second 5xx fails with a bounded reason. Connection-level failures
    stay the declared skip. A malformed 200 body (captive portal,
    truncated read) fails bounded instead of an uncaught traceback.
    """
    for attempt in (1, 2):
        try:
            payload: object = _probe_once()
        except urllib.error.HTTPError as exc:
            if exc.code < 500 or attempt == 2:
                _fail(f"PyPI index (HTTP {exc.code} for {PACKAGE})", "")
            print(
                f"{PREFIX} PyPI answered HTTP {exc.code}; retry {attempt + 1} of 2 "
                f"in {PROBE_RETRY_BACKOFF_S:.0f}s.",
                flush=True,
            )
            time.sleep(PROBE_RETRY_BACKOFF_S)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"{PREFIX} SKIP: PyPI not reachable ({exc}).", flush=True)
            raise SystemExit(0) from None
        except (ValueError, http.client.HTTPException) as exc:
            _fail("PyPI index body (malformed 200 answer)", str(exc))
        else:
            info = payload.get("info") if isinstance(payload, dict) else None
            version = str(info.get("version")) if isinstance(info, dict) else "unknown"
            print(f"{PREFIX} PyPI reachable (latest published version: {version}).", flush=True)
            return


def _venv_layout() -> tuple[str, str]:
    """(interpreter path, console-script path) inside a venv, this platform."""
    if sys.platform.startswith("win"):
        return "Scripts/python.exe", "Scripts/corpus-kb.exe"
    return "bin/python", "bin/corpus-kb"


def _create_venv(root: Path, deadline: float) -> Path:
    """Create the throwaway venv with the running interpreter."""
    py_rel, _ = _venv_layout()
    venv = root / "venv"
    code, tail = _run([sys.executable, "-m", "venv", str(venv)], _remaining(deadline))
    if code != 0 or not (venv / py_rel).exists():
        _fail(f"venv create (exit {code})", tail or f"interpreter missing at {venv / py_rel}")
    ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    print(f"{PREFIX} venv create: ok ({ver})", flush=True)
    return venv


def _pip(venv: Path, deadline: float, label: str, *args: str) -> None:
    """One venv-pip step; bounded tail on failure, one ok line on success."""
    py_rel, _ = _venv_layout()
    started = time.monotonic()
    code, tail = _run(
        [str(venv / py_rel), "-m", "pip", *args, "-q"],
        min(INSTALL_TIMEOUT_S, _remaining(deadline)),
        tail_lines=60,
    )
    if code != 0:
        _fail(f"{label} (exit {code})", tail)
    print(f"{PREFIX} {label}: ok ({time.monotonic() - started:.0f}s)", flush=True)


def _cli_check(
    venv: Path,
    deadline: float,
    label: str,
    argv: list[str],
    ok_codes: set[int],
    marker: str | None,
) -> bool:
    """One CLI assertion; prints its own PASS/FAIL line and returns the verdict."""
    _, cli_rel = _venv_layout()
    # 200 lines: the --help pages list every subcommand; the "Usage"
    # marker sits near the top, so a short tail could cut it off.
    code, out = _run(
        [str(venv / cli_rel), *argv],
        min(CLI_TIMEOUT_S, _remaining(deadline)),
        tail_lines=200,
    )
    ok = code in ok_codes and (marker is None or marker in out)
    if ok:
        found = f', "{marker}" found' if marker else ""
        print(f"{PREFIX} {label}: PASS (exit {code}{found})", flush=True)
        return True
    why = f"exit {code} not in {sorted(ok_codes)}"
    if marker is not None and marker not in out:
        why += f'; "{marker}" missing from output'
    print(f"{PREFIX} {label}: FAIL ({why})", flush=True)
    if out:
        print(f"{PREFIX} {label} output tail:\n{out}")
    return False


def main(argv: list[str] | None = None) -> int:
    """Run the gate; exit 0 on pass or declared skip, 1 on any failure."""
    del argv  # the gate takes no options; poe calls it bare
    deadline = time.monotonic() + BUDGET_S
    _probe_pypi()
    root = Path(tempfile.mkdtemp(prefix="corpus-kb-pypi-smoke-"))
    results: list[bool] = []
    try:
        venv = _create_venv(root, deadline)
        _pip(venv, deadline, "pip upgrade", "install", "--upgrade", "pip")
        _pip(venv, deadline, f"pip install {PACKAGE}", "install", PACKAGE)
        _, cli_rel = _venv_layout()
        if not (venv / cli_rel).exists():
            _fail("console script", f"{venv / cli_rel} missing after a successful install")
        results = [
            _cli_check(venv, deadline, "--help", ["--help"], {0}, MARKER),
            _cli_check(venv, deadline, "doctor", ["doctor"], {0, 3}, None),
            _cli_check(venv, deadline, "demo-kb --help", ["demo-kb", "--help"], {0}, None),
        ]
    finally:
        shutil.rmtree(root, ignore_errors=True)
        if root.exists():
            print(f"{PREFIX} note: temp venv cleanup failed (left at {root}).")
    failed = results.count(False)
    if failed:
        print(f"{PREFIX} FAIL ({failed} of {len(results)} checks failed).", flush=True)
        return 1
    print(f"{PREFIX} PASS.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
