"""Install the Corpus-KB engine from a release tag (issue #337).

``corpus-rag engine install`` clones this repository at the tag
``corpus-rag-v<plugin version>`` into ``<state>/engine`` and runs
``uv sync --frozen --no-dev`` inside the clone. ``engine_paths.py``
already resolves that folder; this module is its producer.

Rules:

- Never rewrite lockfiles: the sync runs with ``--frozen``.
- Checks run before anything moves. A missing release tag is a typed
  error that lists the nearest available tags; it never falls back to
  another branch or tag.
- ``--force`` reinstalls. The previous install moves to
  ``<state>/engine-versions/<version>-<timestamp>/`` for rollback.
  Nothing is deleted.
- A fresh clone whose clone or sync step fails is removed, so failed
  retries never pile up broken copies.
- OS-level faults (locked files, full disks) surface as the same typed
  error, and the message says where each folder ended up.

Stdlib only. State lives under the engine state dir
(``CORPUS_KB_STATE_DIR``, else ``~/.corpus-kb``).
"""

from __future__ import annotations

import datetime
import json
import re
import shutil
import subprocess
from pathlib import Path

from engine_paths import PLUGIN_ROOT, state_dir

TAG_PREFIX = "corpus-rag-v"
MARKER_NAME = ".corpus-rag-engine.json"
VERSIONS_DIRNAME = "engine-versions"
NEAREST_TAG_COUNT = 5
TAG_TIMEOUT = 120
CLONE_TIMEOUT = 900
SYNC_TIMEOUT = 1800

_VERSION_RE = re.compile(r"^corpus-rag-v(\d+)\.(\d+)\.(\d+)$")


class EngineInstallError(Exception):
    """A typed install failure. The message is user-facing."""


def _plugin_manifest() -> dict[str, object]:
    path = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
    try:
        return dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise EngineInstallError(
            "The plugin manifest is missing or broken. Reinstall the plugin."
        ) from exc


def plugin_version() -> str:
    version = _plugin_manifest().get("version")
    if not version:
        raise EngineInstallError("The plugin manifest has no version. Reinstall the plugin.")
    return str(version)


def plugin_repo_url() -> str:
    url = _plugin_manifest().get("repository")
    if not url:
        raise EngineInstallError("The plugin manifest has no repository URL. Reinstall the plugin.")
    return str(url)


def install_target() -> Path:
    """The active install folder the resolution chain checks."""
    return state_dir() / "engine"


def versions_dir() -> Path:
    """Rollback folder: one timestamped subfolder per kept older install."""
    return state_dir() / VERSIONS_DIRNAME


def installed_version(target: Path) -> str | None:
    try:
        data = json.loads((target / MARKER_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    version = data.get("version")
    return str(version) if version else None


def _tag_key(tag: str) -> tuple[int, int, int] | None:
    match = _VERSION_RE.match(tag)
    if match is None:
        return None
    return (int(match[1]), int(match[2]), int(match[3]))


def nearest_tags(tags: list[str], target_tag: str, limit: int = NEAREST_TAG_COUNT) -> list[str]:
    """Release tags nearest to ``target_tag``, nearest first."""
    target = _tag_key(target_tag) or (0, 0, 0)

    def distance(tag: str) -> tuple[int, str]:
        key = _tag_key(tag)
        if key is None:
            return (10**9, tag)
        gap = abs(key[0] - target[0]) + abs(key[1] - target[1]) + abs(key[2] - target[2])
        return (gap, tag)

    ranked = sorted(tags, key=distance)
    return ranked[:limit]


def _run(
    cmd: list[str], cwd: Path | None = None, timeout: int = TAG_TIMEOUT
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            cwd=str(cwd) if cwd is not None else None,
        )
    except FileNotFoundError as exc:
        raise EngineInstallError(
            f"Command not found: {cmd[0]}. Install it, then run this command again."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise EngineInstallError(f"Command timed out after {timeout} seconds: {cmd[0]}.") from exc


def _tail(head: str, result: subprocess.CompletedProcess[str], lines: int = 10) -> str:
    output = (result.stdout or result.stderr or "").strip()
    if not output:
        return head
    return head + "\n" + "\n".join(output.splitlines()[-lines:])


def available_tags(repo_url: str) -> list[str]:
    result = _run(["git", "ls-remote", "--tags", repo_url])
    if result.returncode != 0:
        raise EngineInstallError(_tail("Could not read tags from the repository.", result))
    tags: set[str] = set()
    for line in result.stdout.splitlines():
        ref = line.partition("refs/tags/")[2]
        if ref and not ref.endswith("^{}") and ref.startswith(TAG_PREFIX):
            tags.add(ref)
    return sorted(tags)


def _backup_existing(target: Path, old_version: str | None) -> Path:
    """Move the current install into the rollback folder. Nothing is deleted."""
    now = datetime.datetime.now(datetime.UTC)
    base = f"{old_version or 'unknown'}-{now:%Y%m%dT%H%M%S%f}"
    backup = versions_dir() / base
    counter = 0
    while backup.exists():  # same-microsecond collision guard
        counter += 1
        backup = versions_dir() / f"{base}-{counter}"
    try:
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(backup))
    except OSError as exc:
        raise EngineInstallError(
            f"Could not move the old install to {backup}. "
            "Close programs that use it, then run this command again."
        ) from exc
    return backup


def _write_marker(target: Path, version: str, tag: str) -> None:
    now = datetime.datetime.now(datetime.UTC)
    payload = {"version": version, "tag": tag, "installed_at": now.isoformat(timespec="seconds")}
    try:
        (target / MARKER_NAME).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise EngineInstallError(f"Could not write the install marker in {target}.") from exc


def _discard_clone(target: Path) -> bool:
    """Remove a broken fresh clone. Return True when removal worked."""
    try:
        shutil.rmtree(target)
    except OSError:
        return False
    return True


def _fail_after_clone(head: str, result: subprocess.CompletedProcess[str], target: Path) -> None:
    """Surface a clone/sync failure after removing the useless fresh clone."""
    message = _tail(head, result)
    if _discard_clone(target):
        message += (
            f"\nThe failed clone was removed from {target}.\n"
            "Fix the problem, then run this command again."
        )
    else:
        message += (
            f"\nThe broken clone is still at {target}.\n"
            "Remove it by hand, then run this command again."
        )
    raise EngineInstallError(message)


def _attach_backup_note(message: str, backup: Path | None) -> str:
    if backup is None:
        return message
    return f"{message}\nThe previous install is safe at {backup}."


def run_install(force: bool = False, version: str | None = None) -> str:
    """Install the engine. Return a user-facing message.

    Raise ``EngineInstallError`` when the install cannot proceed.
    """
    resolved = version if version is not None else plugin_version()
    tag = f"{TAG_PREFIX}{resolved}"
    url = plugin_repo_url()
    target = install_target()

    if shutil.which("uv") is None:
        raise EngineInstallError(
            "uv is not on PATH. Install uv (https://docs.astral.sh/uv/), "
            "then run this command again."
        )

    old: str | None = None
    if target.exists():
        old = installed_version(target)
        if old == resolved and not force:
            return (
                f"ok: engine {resolved} is already installed at {target}. "
                "Run with --force to reinstall."
            )

    tags = available_tags(url)  # nothing has moved yet
    if tag not in tags:
        if tags:
            near = ", ".join(nearest_tags(tags, tag))
            raise EngineInstallError(
                f"Tag {tag} does not exist on {url}. Nearest tags: {near}. "
                "Release tags are cut at release time. "
                "Install the engine by hand; see docs/PLUGIN.md."
            )
        raise EngineInstallError(
            f"No {TAG_PREFIX}* tags exist on {url}. Release tags are cut at release time. "
            "Install the engine by hand; see docs/PLUGIN.md."
        )

    backup: Path | None = None
    if target.exists():
        backup = _backup_existing(target, old)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        clone = _run(
            ["git", "clone", "--depth", "1", "--branch", tag, url, str(target)],
            timeout=CLONE_TIMEOUT,
        )
        if clone.returncode != 0:
            _fail_after_clone(f"Could not clone tag {tag}.", clone, target)
        sync = _run(["uv", "sync", "--frozen", "--no-dev"], cwd=target, timeout=SYNC_TIMEOUT)
        if sync.returncode != 0:
            _fail_after_clone("uv sync failed.", sync, target)
        _write_marker(target, resolved, tag)
    except EngineInstallError as exc:
        raise EngineInstallError(_attach_backup_note(str(exc), backup)) from exc
    except OSError as exc:
        raise EngineInstallError(
            _attach_backup_note(f"Install failed in {target}: {exc}", backup)
        ) from exc

    message = f"ok: engine {resolved} installed at {target} from tag {tag}."
    if backup is not None:
        message += f"\nKept the previous install at {backup}."
    return message
