"""Packaged-config lockstep gate (issue #246).

``get_default_config()`` — the code-side defaults, including the typed
modules' blocks — and the PACKAGED ``src/corpus_kb/config.yaml`` are two
renderings of one contract: "what a default is". The loader deep-merges
the packaged yaml OVER the code defaults, so a leaf that disagrees
between the two changes behavior with the deployment shape. This module
walks both trees and reports every leaf difference, so the sources
cannot drift again.

The check runs in two places:
  - ``validate_configs.validate_all`` calls ``check_packaged_lockstep``
    (poe ``validate-configs``; the CI job of the same name). That job
    installs only PyYAML, so when ``corpus_kb`` cannot import there the
    check reports a clean skip.
  - ``tests/test_config_lockstep.py`` calls the same function with the
    full package installed. That test job is the hard CI enforcement.

This module imports stdlib plus PyYAML only, so both contexts can load
it. ``backend_mix_errors`` texts live in ``corpus_kb.config``; this gate
compares VALUES, never strings.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import yaml


class LockstepResult(NamedTuple):
    """Outcome of one packaged-lockstep check.

    skipped: True when the environment cannot import ``corpus_kb``
        (the PyYAML-only CI job); errors is empty then and the pytest
        suite owns enforcement.
    errors: One leaf-precise string per drifted leaf; empty = lockstep.
    summary: One-line human summary (counts or the skip reason).
    """

    skipped: bool
    errors: list[str]
    summary: str


def flatten_leaves(tree: object, prefix: str = "") -> dict[str, object]:
    """Flatten a config tree to ``path -> value`` leaves.

    Dicts recurse; every other node — scalars, lists, None — is ONE leaf
    (a list is a single config value, compared whole).
    """
    leaves: dict[str, object] = {}
    if isinstance(tree, dict):
        for key, value in tree.items():
            child = f"{prefix}{key}." if prefix else f"{key}."
            leaves.update(flatten_leaves(value, child))
        return leaves
    leaves[prefix.rstrip(".")] = tree
    return leaves


def _render(value: object) -> str:
    return repr(value)


def leaf_diffs(code: dict[str, object], packaged: dict[str, object]) -> list[str]:
    """Compare the two trees leaf-by-leaf; return one message per drift.

    Message shapes (paths are dotted; values are repr'd):
      ``extraction.backend: code='pgml' packaged='ml'``
      ``analysis.labeling.enabled: missing from packaged config.yaml (code=False)``
      ``extraction.mode: missing from get_default_config() (packaged='zero_shot')``
    Order is stable: value diffs, then code-only, then yaml-only, each
    sorted by path — deterministic output for snapshots (program rule 9).
    """
    code_leaves = flatten_leaves(code)
    packaged_leaves = flatten_leaves(packaged)
    diffs: list[str] = []
    for path in sorted(set(code_leaves) & set(packaged_leaves)):
        if code_leaves[path] != packaged_leaves[path]:
            diffs.append(
                f"{path}: code={_render(code_leaves[path])} "
                f"packaged={_render(packaged_leaves[path])}"
            )
    for path in sorted(set(code_leaves) - set(packaged_leaves)):
        diffs.append(
            f"{path}: missing from packaged config.yaml (code={_render(code_leaves[path])})"
        )
    for path in sorted(set(packaged_leaves) - set(code_leaves)):
        diffs.append(
            f"{path}: missing from get_default_config() (packaged={_render(packaged_leaves[path])})"
        )
    return diffs


def packaged_yaml_path() -> Path:
    """The packaged config.yaml, resolved next to this module.

    Works from a source checkout (script context) and from an installed
    wheel: ``corpus_kb/_setup/`` and ``corpus_kb/config.yaml`` ship as
    real files in both.
    """
    return Path(__file__).resolve().parents[1] / "config.yaml"


def _load_yaml_dict(path: Path) -> dict[str, object]:
    """Load a config YAML file; an empty file yields an empty dict."""
    loaded: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def check_packaged_lockstep(
    code_config: dict[str, object] | None = None,
    packaged: dict[str, object] | None = None,
) -> LockstepResult:
    """Run the full leaf-equality check; never raises for drift.

    Args:
        code_config: Override for ``get_default_config()`` (tests).
        packaged: Override for the packaged yaml tree (tests).

    Returns a LockstepResult; skipped=True means this environment cannot
    import ``corpus_kb`` (ModuleNotFoundError, the PyYAML-only CI job).
    """
    code_tree: dict[str, object] | None = code_config
    packaged_tree: dict[str, object] | None = packaged
    if code_tree is None or packaged_tree is None:
        try:
            from corpus_kb.config import get_default_config
        except ImportError:
            return LockstepResult(
                skipped=True,
                errors=[],
                summary=(
                    "config-lockstep: skipped here (corpus_kb not importable); "
                    "enforced by the pytest suite (tests/test_config_lockstep.py)"
                ),
            )
        if code_tree is None:
            code_tree = get_default_config()
        if packaged_tree is None:
            path = packaged_yaml_path()
            if not path.exists():
                return LockstepResult(
                    skipped=False,
                    errors=[f"{path}: packaged config.yaml not found"],
                    summary="config-lockstep: FAILED (packaged yaml missing)",
                )
            packaged_tree = _load_yaml_dict(path)

    errors = leaf_diffs(code_tree, packaged_tree)
    leaf_count = len(flatten_leaves(code_tree))
    summary = (
        f"config-lockstep: OK ({leaf_count} leaves equal)"
        if not errors
        else f"config-lockstep: FAILED ({len(errors)} diffs)"
    )
    return LockstepResult(skipped=False, errors=errors, summary=summary)
