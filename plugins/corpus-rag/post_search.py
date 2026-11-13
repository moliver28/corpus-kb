"""The generic ``post_search`` extension point (issue #355).

After ``corpus-rag search`` prints its hits, every registered command
runs with the search context and may print more. The registry is one
JSON file under the plugin data folder; ``corpus-rag`` learns nothing
about the registering module. A registered command is a CLI subcommand
path, like ``uxr post-search``; it receives
``--post-search-json '{"slug": ..., "query": ...}'``.

Fail-open: a broken registered command records the error and prints
nothing. Stdlib only.
"""

from __future__ import annotations

import contextlib
import io
import json
from collections.abc import Callable
from pathlib import Path

import hook_state
import registry_io
from engine_paths import plugin_data_dir
from file_locks import exclusive

FILENAME = "post-search.json"
LOCK_SUFFIX = ".lock"


def registry_path():
    return plugin_data_dir() / FILENAME


def commands() -> list[str]:
    """The registered command names, in registration order."""
    try:
        data = json.loads(registry_io.read_with_retry(registry_path()))
    except (OSError, ValueError):
        return []
    rows = data.get("commands") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    return [str(row) for row in rows if isinstance(row, str) and row.strip()]


def register(name: str) -> bool:
    """Add one command. False when it was already registered."""
    added = False

    def add(current: list[str]) -> list[str]:
        nonlocal added
        if name in current:
            return current
        added = True
        return [*current, name]

    mutate(add)
    return added


def unregister(name: str) -> None:
    mutate(lambda current: [row for row in current if row != name])


def mutate(change: Callable[[list[str]], list[str]]) -> None:
    """Change the command list under one lock held across read and write.

    The lock spans the read and the save, so two registering runs cannot
    lose one command. ``_save`` runs inside the span; it never re-locks.
    """
    with exclusive(Path(str(registry_path()) + LOCK_SUFFIX)):
        _save({"commands": change(commands())})


def _save(data: dict) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    registry_io.replace_with_retry(tmp, path)


def run_registered(slug: str, query: str) -> int:
    """Run every registered command; print what they print.

    Returns how many commands produced output. A command that is not on
    the CLI (yet), raises, or prints nothing stays silent: search never
    breaks because of an extension.
    """
    produced = 0
    for name in commands():
        try:
            output = _run_one(name, slug, query)
        except Exception as exc:  # fail open, like the hooks
            hook_state.record_error("post_search", f"{name}: {exc!r}")
            continue
        if output:
            produced += 1
            print(output)
    return produced


def _run_one(name: str, slug: str, query: str) -> str:
    import cli_spec

    parts = name.split()
    if not parts or parts[0] not in cli_spec.HANDLERS:
        raise LookupError(f"no command named {name!r}")
    payload = json.dumps({"slug": slug, "query": query})
    try:
        args = cli_spec.parse_argv([*parts, "--post-search-json", payload])
    except SystemExit as exc:
        raise LookupError(f"command {name!r} does not accept the post-search context") from exc
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        cli_spec.HANDLERS[parts[0]](args)
    return buffer.getvalue().rstrip("\n")
