"""The calibrate apply op: ONE handler, every surface (issue #200).

A calibration sweep writes ``<profile>.lock.json``; the lock carries the
winner's ``proposed_overrides``. Before this op, applying a win meant a
manual copy into ``config.yaml`` (docs/VALIDATION.md Step 5). This module
is the one seam for that copy: the CLI, the HTTP route, the socket arm,
and the MCP tool all call :func:`calibrate_apply`.

Reuse, never a second pipeline: the lock comes from ``eval.lockfile``
(drift check through ``check_profile_lock``), the key allow-list comes
from ``eval.recipes`` (validation through ``apply_overrides``), and the
profile target is containment-checked with ``safe_profile_path``. A
``--dry-run`` (the default) computes the diff and writes nothing.

The write is a targeted text edit: only the changed lines move, so
comments and formatting in the user's config.yaml survive. Every change
row carries an ``edit`` outcome — ``replaced``, ``inserted`` (a
no-replace edit: a fresh line went in and an old line may remain),
``created`` (the containing block is new), or ``none`` (nothing to
change) — so the operator sees a missed text anchor (issue #341).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import yaml

from corpus_kb.errors import ConfigError, ValidationError
from corpus_kb.tools.config_edit import apply_keys_tracked, write_atomic

__all__ = ["calibrate_apply"]


async def calibrate_apply(
    profile: str,
    write: bool = False,
    config: dict[str, Any] | None = None,
) -> dict[str, object]:
    """Apply one profile lock's ``proposed_overrides`` to config.yaml.

    ``write=False`` (the default) previews the diff and touches nothing.
    ``write=True`` rewrites the changed lines in the user config file.
    Returns the ``{"status", "result"}`` envelope every surface shares.
    Raises a typed ``CorpusError`` subclass on every refusal.
    """
    if config is None:
        from corpus_kb.config import load_config

        config = load_config()
    from corpus_kb.eval.lockfile import check_profile_lock
    from corpus_kb.eval.recipes import apply_overrides
    from corpus_kb.profiles.loader import profiles_dir, safe_profile_path

    root = Path(str(profiles_dir(config)))
    profile_path = Path(safe_profile_path(str(root), f"{profile}.yaml"))
    if not profile_path.is_file():
        raise ValidationError(
            f"Profile {profile!r} not found at {profile_path}.",
            remediation="Name a profile that exists in the profiles directory.",
        )
    lock_path = root / f"{profile}.lock.json"
    if not lock_path.is_file():
        raise ValidationError(
            f"No calibration lock for profile {profile!r}.",
            remediation="Run the sweep first: ingest with validate=calibrate.",
        )
    # Drift aid: a profile edited after its sweep must not get stale values.
    check_profile_lock(profile_path, lock_path)
    lock = _read_lock(lock_path)
    proposed = lock.get("proposed_overrides")
    if not isinstance(proposed, dict):
        raise ValidationError(
            f"Lock {lock_path.name} carries no proposed_overrides object.",
            remediation="Re-run the sweep to regenerate the lock.",
        )
    outcome = lock.get("outcome")
    if outcome is not None and outcome != "success":
        raise ValidationError(
            f"Lock {lock_path.name} records outcome {outcome!r}, not success.",
            remediation="Only a success lock carries proven overrides. Re-run the sweep.",
        )
    config_path, config_text, file_config = _read_config(_config_path())
    # apply_overrides IS the recipes allow-list: unknown keys raise a
    # typed ValidationError here, on every surface.
    merged = apply_overrides(file_config, proposed)
    changes = _diff(file_config, merged, sorted(proposed))
    applied_count = 0
    edits: dict[str, str] = {}
    dirty = {str(row["key"]): row["proposed"] for row in changes if row["changed"]}
    if dirty:
        # The edit runs on the SAME text the diff came from, so the
        # diff, the receipt, and the write can never disagree. The dry
        # run edits nothing on disk, but it still reports what each
        # edit would do to the file text.
        new_text, edits = apply_keys_tracked(config_text, dirty)
        if write:
            try:
                write_atomic(config_path, new_text)
            except OSError as exc:
                # Surface adapters must never see a raw OSError here: it
                # would print as a database fault on the CLI.
                raise ConfigError(
                    f"Cannot write the config file {config_path.name}.",
                    remediation=(
                        "Check the file permissions and free disk space, then run the apply again."
                    ),
                ) from exc
            applied_count = len(dirty)
    for row in changes:
        row["edit"] = edits.get(str(row["key"]), "none")
    return {
        "status": "success",
        "result": {
            "profile": profile,
            "outcome": outcome,
            "chosen": lock.get("chosen"),
            "mode": "write" if write else "dry_run",
            "config_path": str(config_path),
            "changes": changes,
            "applied_count": applied_count,
        },
    }


def _read_lock(lock_path: Path) -> dict[str, object]:
    """Parse the lock payload.

    ``check_profile_lock`` has already proven the file is readable JSON
    with an object payload, so this parse cannot fail. Only the fields
    enforcement does not read live here.
    """
    lock: object = json.loads(lock_path.read_text(encoding="utf-8"))
    return cast("dict[str, object]", lock)


def _config_path() -> Path | None:
    """The user's config file, resolved at call time (tests patch this seam)."""
    from corpus_kb.config import config_file_path

    return config_file_path()


def _read_config(config_path: Path | None) -> tuple[Path, str, dict[str, object]]:
    """Read and parse the user's config file; every failure is a ConfigError.

    Returns the resolved path (narrowed non-None), the raw text, and the
    parsed tree. The write path reuses the SAME text, so the diff and
    the edit agree and the file is read once.
    """
    if config_path is None:
        raise ConfigError(
            "No user config.yaml found to apply the lock into.",
            remediation=(
                "Create config.yaml in the working directory, or point CORPUS_KB_CONFIG at it."
            ),
        )
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            f"Config file {config_path.name} is not readable.",
            remediation="Check the file permissions, then run the apply again.",
        ) from exc
    try:
        loaded: object = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(
            f"Config file {config_path.name} is not readable YAML.",
            remediation="Fix the YAML syntax, then run the apply again.",
        ) from exc
    if not isinstance(loaded, dict):
        raise ConfigError(
            f"Config file {config_path.name} is not a YAML mapping.",
            remediation="Give the file one top-level mapping of config blocks.",
        )
    typed: dict[str, object] = {str(key): value for key, value in loaded.items()}
    return config_path, text, typed


def _diff(
    current: Mapping[str, object], merged: Mapping[str, object], keys: list[str]
) -> list[dict[str, object]]:
    """One row per lock key, sorted; ``changed`` marks a real value move."""
    rows: list[dict[str, object]] = []
    for dot_path in keys:
        block, _, key = dot_path.partition(".")
        proposed = _leaf(merged, block, key)
        rows.append(
            {
                "key": dot_path,
                "current": _leaf(current, block, key),
                "proposed": proposed,
                "changed": _leaf(current, block, key) != proposed,
            }
        )
    return rows


def _leaf(config: Mapping[str, object], block: str, key: str) -> object:
    """One config leaf by dotted key; ``key`` may nest into child blocks."""
    node = config.get(block)
    if not isinstance(node, Mapping):
        return None
    parts = key.split(".")
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, Mapping):
            return None
        node = child
    return node.get(parts[-1])
