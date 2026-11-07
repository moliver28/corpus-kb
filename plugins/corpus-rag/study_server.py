"""Per-study server manager (issue #351).

One ``corpus-kb start`` per study: HTTP on the study's port, with its
database URL and backend, its socket path, and the config overlay in the
environment. The process detaches and logs to ``<data>/logs/<slug>.log``.

Health probe: ``GET /api/doctor`` then ``GET /api/stats``, once a second,
up to 90 seconds. If the process exits first, the error prints the log
path and its last lines.

Every command writes ``<data>/servers/<slug>.last_used``. Other studies'
servers idle over 30 minutes are stopped; never the current one. Each
server holds 5 to 20 Postgres connections, so idle ones must go.

DuckDB allows one writer per file, so migrate, reembed, doctor, and
backup stop the study server first and wait up to 30 seconds.

Stdlib only. ``subprocess`` and HTTP seams are module functions that
tests replace.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import op_client
import study_registry
from engine_paths import plugin_data_dir
from process_utils import as_pid, pid_alive, terminate_pid
from study_config import study_env

START_TIMEOUT_DEFAULT = 90.0
STOP_GRACE_DEFAULT = 30.0
IDLE_LIMIT_SECONDS = 30 * 60
PROBE_PATHS = ("/api/doctor", "/api/stats")


class ServerError(Exception):
    """A user-facing server failure."""


def logs_dir() -> Path:
    return plugin_data_dir() / "logs"


def servers_dir() -> Path:
    return plugin_data_dir() / "servers"


def last_used_path(slug: str) -> Path:
    return servers_dir() / f"{slug}.last_used"


def base_url(study: dict) -> str:
    return f"http://127.0.0.1:{int(study.get('port') or 0)}"


def note_use(slug: str) -> None:
    """Mark this study's server as used right now."""
    path = last_used_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(time.time()), encoding="utf-8")


def last_used_age(slug: str) -> float | None:
    """Seconds since the last use mark; None when never used."""
    path = last_used_path(slug)
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def note_use_and_reap(registry: dict, slug: str) -> list[str]:
    """Mark use, then stop other studies' servers idle over 30 minutes."""
    note_use(slug)
    return reap_idle(registry, slug)


def reap_idle(
    registry: dict,
    current_slug: str,
    idle_seconds: float = IDLE_LIMIT_SECONDS,
    stopper=None,
) -> list[str]:
    """Stop idle servers of other studies. Never the current one."""
    stopped: list[str] = []
    for slug, study in registry.get("studies", {}).items():
        if slug == current_slug:
            continue
        age = last_used_age(slug)
        if age is None or age <= idle_seconds:
            continue
        do_stop = stopper if stopper is not None else stop_study
        if do_stop(slug, study):
            stopped.append(slug)
    return stopped


def probe(port: int, timeout: float = 5.0) -> bool:
    """True when the study server answers /api/doctor or /api/stats."""
    for path in PROBE_PATHS:
        try:
            op_client.get_json(f"http://127.0.0.1:{port}{path}", timeout=timeout)
        except op_client.HttpError:
            continue
        return True
    return False


def wait_healthy(
    port: int,
    pid: int | None,
    timeout: float,
    sleep=time.sleep,
    probe_fn=None,
    alive_fn=None,
) -> bool:
    """Probe once a second until healthy or the timeout passes.

    Returns False early when the process exits before health arrives.
    """
    check = probe_fn if probe_fn is not None else probe
    alive = alive_fn if alive_fn is not None else pid_alive
    deadline = time.monotonic() + timeout
    while True:
        if check(port):
            return True
        if pid is not None and not alive(pid):
            return False
        if time.monotonic() >= deadline:
            return False
        sleep(1.0)


def _log_tail(log_path: Path, lines: int = 10) -> str:
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


def start(cli: str, slug: str, study: dict, wait: float | None = None) -> int:
    """Start one study server detached; return its pid.

    Raises ``ServerError`` with the log path and last lines when the
    process exits first or never turns healthy.
    """
    port = int(study.get("port") or 0)
    log_path = logs_dir() / f"{slug}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = study_env(slug, study)
    command = [cli, "start", "--transport", "http", "--port", str(port), "--no-ui"]
    popen_kwargs: dict[str, object] = {"start_new_session": os.name != "nt"}
    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
            close_fds=True,
            **popen_kwargs,
        )
    pid = process.pid
    timeout = START_TIMEOUT_DEFAULT if wait is None else wait
    if wait_healthy(port, pid, timeout):
        return pid
    tail = _log_tail(log_path)
    if not pid_alive(pid):
        raise ServerError(
            f"The study server for '{slug}' exited during start. Log: {log_path}\n{tail}"
        )
    terminate_pid(pid, grace=5.0)
    raise ServerError(
        f"The study server for '{slug}' did not turn healthy in {timeout:g}s. "
        f"Log: {log_path}\n{tail}"
    )


def stop_study(slug: str, study: dict, grace: float = STOP_GRACE_DEFAULT) -> bool:
    """Stop one study's server; clear the pid in the row. True when stopped."""
    pid = as_pid(study.get("pid"))
    if pid is None or not pid_alive(pid):
        study["pid"] = None
        return False
    terminate_pid(pid, grace=grace)
    study["pid"] = None
    return True


def ensure_running(cli: str, registry: dict, slug: str, study: dict) -> dict:
    """The study's server up; persist pid or port changes. Returns the row."""
    port = int(study.get("port") or 0)
    pid = as_pid(study.get("pid"))
    if pid is not None and pid_alive(pid):
        if probe(port):
            return study
        if wait_healthy(port, pid, 10.0):
            return study
        stop_study(slug, study)
        pid = None
    if not port:
        study["port"] = study_registry.allocate_port(registry)
    elif pid is None and not study_registry.port_free(port):
        # A foreign process holds the port; move this study to a free one.
        study["port"] = study_registry.allocate_port(registry)
    study["pid"] = start(cli, slug, study)
    study_registry.save_registry(registry)
    return study


def stop_before(slug: str, study: dict, reason: str, grace: float = STOP_GRACE_DEFAULT) -> bool:
    """DuckDB allows one writer per file: stop the study server first.

    Postgres needs no stop; this returns False without touching it. The
    caller persists the cleared pid when it saves the registry.
    """
    if str(study.get("backend", "")) != "duckdb":
        return False
    if stop_study(slug, study, grace=grace):
        print(f"Stopped the '{slug}' server first ({reason}); DuckDB allows one writer.")
        return True
    return False
