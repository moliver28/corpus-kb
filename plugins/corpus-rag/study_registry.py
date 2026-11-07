"""The study registry: one slug, one database, one port (issue #351).

File: ``<plugin data>/studies.json``. Keys: ``active`` and ``studies``
(``{slug: {database_url, port, backend, pid, paradigm?}}``). Writes hold
a lock file and land with an atomic rename (see ``registry_io``). On
first run a 0.6.0 ``<state>/projects.json`` (``projects{slug: {db, port,
pid, backend}}``) imports into the new file; the old file stays.

Study selection, first match wins (issue #351):

1. ``-p <slug>`` when given.
2. The slug in the folder's ``.claude/corpus.json``, searched upward.
3. The registry ``active`` study.
4. Nothing: raise and tell the user to name one.

Slug rule: lowercase; non-alphanumerics become ``_``; repeats collapse;
edge ``_`` strips. Database name ``corpus_kb_<slug>``. DuckDB file
``<data>/duckdb/corpus_kb_<slug>.duckdb``.

Stdlib only.
"""

from __future__ import annotations

import json
import re
import socket
import threading
from pathlib import Path

import registry_io
from engine_paths import plugin_data_dir, state_dir
from study_config import base_port

REGISTRY_FILENAME = "studies.json"
LEGACY_FILENAME = "projects.json"
LOCK_SUFFIX = ".lock"
TMP_SUFFIX = ".tmp"
PORT_SEARCH_LIMIT = 200
NO_STUDY_MESSAGE = "Name a study with -p, or run `corpus-rag use <slug>`."

_process_lock = threading.Lock()


class RegistryError(Exception):
    """A user-facing registry failure."""


def data_dir() -> Path:
    """The plugin data folder that holds the registry."""
    return plugin_data_dir()


def registry_path() -> Path:
    return data_dir() / REGISTRY_FILENAME


def legacy_path() -> Path:
    """The 0.6.0 ``projects.json`` that imports on first run."""
    return state_dir() / LEGACY_FILENAME


def empty_registry() -> dict:
    return {"active": None, "studies": {}}


def slugify(raw: str) -> str:
    """Lowercase; non-alphanumerics become ``_``; repeats collapse; edges strip."""
    slug = re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", raw.lower())).strip("_")
    if not slug:
        raise RegistryError(f"'{raw}' makes an empty study slug. Use letters and numbers.")
    return slug


def database_name(slug: str) -> str:
    return f"corpus_kb_{slug}"


def duckdb_file(slug: str) -> Path:
    return data_dir() / "duckdb" / f"{database_name(slug)}.duckdb"


def load_registry() -> dict:
    """Read the registry; import a 0.6.0 ``projects.json`` on first run."""
    path = registry_path()
    if not path.is_file():
        imported = _import_legacy()
        if imported is not None:
            save_registry(imported)
            return imported
        return empty_registry()
    try:
        registry = json.loads(registry_io.read_with_retry(path))
    except ValueError as exc:
        raise RegistryError(
            f"The study registry at {path} is not valid JSON. Fix or delete the file."
        ) from exc
    if not isinstance(registry, dict):
        raise RegistryError(f"The study registry at {path} must hold a JSON object.")
    registry.setdefault("active", None)
    registry.setdefault("studies", {})
    return registry


def _import_legacy() -> dict | None:
    """Map 0.6.0 ``projects`` rows to the new ``studies`` shape."""
    legacy = legacy_path()
    if not legacy.is_file():
        return None
    try:
        data = json.loads(legacy.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    projects = data.get("projects") if isinstance(data, dict) else None
    if not isinstance(projects, dict) or not projects:
        return None
    studies: dict[str, dict] = {}
    for slug, row in projects.items():
        if not isinstance(row, dict):
            continue
        studies[str(slug)] = {
            "database_url": row.get("db", ""),
            "port": row.get("port"),
            "backend": row.get("backend", "postgres"),
            "pid": row.get("pid"),
        }
    if not studies:
        return None
    registry = empty_registry()
    registry["studies"] = studies
    return registry


def save_registry(registry: dict) -> None:
    """Write the registry: lock file, temp file, atomic rename."""
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = Path(str(path) + LOCK_SUFFIX)
    with _process_lock:
        try:
            registry_io.acquire_lock(lock)
            tmp = Path(str(path) + TMP_SUFFIX)
            tmp.write_text(
                json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            registry_io.replace_with_retry(tmp, path)
        except registry_io.LockBusyError as exc:
            raise RegistryError(str(exc)) from exc
        finally:
            registry_io.release_lock(lock)


def port_free(port: int) -> bool:
    """True when no listener holds the port right now.

    No ``SO_REUSEADDR``: on Windows it would let the probe bind a port
    another server already holds, and hand the same port to two studies.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def allocate_port(registry: dict, base: int | None = None) -> int:
    """First free port from the base port upward, skipping study ports."""
    start = base if base is not None else base_port()
    taken = set()
    for study in registry.get("studies", {}).values():
        try:
            taken.add(int(study.get("port") or 0))
        except (TypeError, ValueError):
            continue
    for port in range(start, start + PORT_SEARCH_LIMIT):
        if port in taken:
            continue
        if port_free(port):
            return port
    raise RegistryError(f"No free port from {start} upward in {PORT_SEARCH_LIMIT} tries.")


def folder_study(start: Path | None = None) -> str | None:
    """The study slug in the nearest ``.claude/corpus.json``, or None."""
    current = (start or Path.cwd()).resolve()
    while True:
        marker = current / ".claude" / "corpus.json"
        if marker.is_file():
            return _marker_slug(marker)
        if current.parent == current:
            return None
        current = current.parent


def _marker_slug(marker: Path) -> str | None:
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    for key in ("study", "slug"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def resolve_study(
    registry: dict, explicit: str | None = None, cwd: Path | None = None
) -> tuple[str, dict]:
    """The 4-step selection. Raises ``RegistryError`` with the hint at step 4."""
    studies = registry.get("studies", {})
    if explicit:
        study = studies.get(explicit)
        if study is None:
            raise RegistryError(
                f"No study named '{explicit}'. Run `corpus-rag list`, "
                f"or `corpus-rag provision {explicit}`."
            )
        return explicit, study
    folder = folder_study(cwd)
    if folder is not None and folder in studies:
        return folder, studies[folder]
    active = registry.get("active")
    if active and active in studies:
        return active, studies[active]
    raise RegistryError(NO_STUDY_MESSAGE)


def register_study(
    registry: dict,
    slug: str,
    database_url: str,
    port: int,
    backend: str,
    pid: int | None = None,
    paradigm: str | None = None,
) -> dict:
    """Add one study row and return it."""
    study: dict[str, object] = {
        "database_url": database_url,
        "port": int(port),
        "backend": backend,
        "pid": pid,
    }
    if paradigm:
        study["paradigm"] = paradigm
    registry.setdefault("studies", {})[slug] = study
    return study


def set_active(registry: dict, slug: str) -> None:
    if slug not in registry.get("studies", {}):
        raise RegistryError(f"No study named '{slug}'. Run `corpus-rag list`.")
    registry["active"] = slug
